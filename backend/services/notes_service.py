from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.core.config import settings
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.storage_utils import read_json_file, write_json_file

NOTES_PATH = Path(settings.storage_dir) / "operator_notes.json"
ALLOWED_PRIORITIES = {"low", "medium", "high"}
ALLOWED_NOTE_TYPES = {"general", "trade_idea", "risk_review", "market_note", "todo"}
ALLOWED_RECURRENCES = {"none", "daily", "weekly", "weekdays", "monthly"}
ALLOWED_SORTS = {
    "updated_desc",
    "updated_asc",
    "created_desc",
    "created_asc",
    "priority",
    "title",
    "due_asc",
    "due_desc",
}


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_now_dt().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _normalize_due_at(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else None


def _normalize_reminder_at(value: Any) -> str | None:
    parsed = _parse_dt(value)
    return parsed.isoformat() if parsed else None


def _normalize_tags(tags: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for tag in tags or []:
        cleaned = str(tag or '').strip().lower()
        if cleaned and cleaned not in seen:
            normalized.append(cleaned)
            seen.add(cleaned)
    return normalized[:12]


def _normalize_ticker(value: str | None) -> str:
    return str(value or '').strip().upper()[:8]


def _normalize_owner(value: str | None) -> str:
    cleaned = ' '.join(str(value or '').strip().split())
    return cleaned[:40]


def _normalize_minutes(value: Any) -> int:
    try:
        numeric = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(numeric, 100000))


def _derive_progress_state(note: dict[str, Any]) -> str:
    if bool(note.get('completed', False)):
        return 'done'
    spent = _normalize_minutes(note.get('spent_minutes'))
    estimate = _normalize_minutes(note.get('estimate_minutes'))
    checklist = _checklist_progress(note)
    if spent > 0 or checklist.get('done', 0) > 0:
        return 'in_progress'
    if estimate > 0:
        return 'planned'
    return 'not_started'


def _derive_progress_percent(note: dict[str, Any]) -> int:
    if bool(note.get('completed', False)):
        return 100
    estimate = _normalize_minutes(note.get('estimate_minutes'))
    spent = _normalize_minutes(note.get('spent_minutes'))
    checklist = _checklist_progress(note)
    if estimate > 0 and spent > 0:
        return max(0, min(int(round((spent / estimate) * 100)), 100))
    if checklist.get('total', 0) > 0:
        return max(0, min(int(round((checklist.get('done', 0) / checklist.get('total', 1)) * 100)), 100))
    return 100 if bool(note.get('completed', False)) else 0


def _normalize_source_url(value: str | None) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        return ''
    if not re.match(r'^https?://', cleaned, flags=re.IGNORECASE):
        cleaned = f'https://{cleaned}'
    return cleaned[:300]


def _normalize_checklist(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in items or []:
        if isinstance(raw, str):
            text = raw.strip()
            done = False
        elif isinstance(raw, dict):
            text = str(raw.get('text') or '').strip()
            done = bool(raw.get('done', False))
        else:
            continue
        if not text:
            continue
        normalized.append({'text': text[:200], 'done': done})
    return normalized[:20]


def _checklist_progress(note: dict[str, Any]) -> dict[str, int]:
    checklist = _normalize_checklist(note.get('checklist'))
    total = len(checklist)
    done = sum(1 for item in checklist if bool(item.get('done', False)))
    return {'total': total, 'done': done, 'open': max(total - done, 0)}



def _normalize_note_id_list(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in values or []:
        cleaned = str(raw or '').strip()[:40]
        if cleaned and cleaned not in seen:
            normalized.append(cleaned)
            seen.add(cleaned)
    return normalized[:20]


def _relation_counts(notes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in notes:
        note_id = str(item.get('id') or '').strip()
        if note_id:
            counts[note_id] = counts.get(note_id, 0) + 1
    return counts


def _derive_blocked_state(note: dict[str, Any], note_ids: set[str] | None = None) -> str:
    blocked_by_ids = _normalize_note_id_list(note.get('blocked_by_ids'))
    if not blocked_by_ids:
        return 'ready'
    note_ids = note_ids or set()
    if any(blocker in note_ids for blocker in blocked_by_ids):
        return 'blocked'
    return 'ready'


def _normalize_recurrence(value: str | None) -> str:
    cleaned = str(value or 'none').strip().lower()
    if cleaned not in ALLOWED_RECURRENCES:
        return 'none'
    return cleaned


def _advance_datetime(value: datetime | None, recurrence: str) -> datetime | None:
    if value is None:
        return None
    recurrence = _normalize_recurrence(recurrence)
    if recurrence == 'daily':
        return value + timedelta(days=1)
    if recurrence == 'weekly':
        return value + timedelta(days=7)
    if recurrence == 'weekdays':
        nxt = value + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if recurrence == 'monthly':
        for days in (28, 29, 30, 31):
            nxt = value + timedelta(days=days)
            if nxt.month != value.month:
                return nxt
        return value + timedelta(days=31)
    return value


def _advance_note_schedule(note: dict[str, Any]) -> dict[str, Any]:
    recurrence = _normalize_recurrence(note.get('recurrence'))
    if recurrence == 'none':
        return note
    recurrence_end_at = _parse_dt(note.get('recurrence_end_at'))
    due_at = _parse_dt(note.get('due_at'))
    reminder_at = _parse_dt(note.get('reminder_at'))
    next_due = _advance_datetime(due_at, recurrence) if due_at else None
    next_reminder = _advance_datetime(reminder_at, recurrence) if reminder_at else None
    if recurrence_end_at:
        if next_due and next_due > recurrence_end_at:
            next_due = None
        if next_reminder and next_reminder > recurrence_end_at:
            next_reminder = None
    note['due_at'] = next_due.isoformat() if next_due else None
    note['reminder_at'] = next_reminder.isoformat() if next_reminder else None
    note['completed'] = False
    if note.get('due_at') is None and note.get('reminder_at') is None:
        note['recurrence'] = 'none'
    return note

def _normalize_priority(value: str | None) -> str:
    cleaned = str(value or 'medium').strip().lower()
    if cleaned not in ALLOWED_PRIORITIES:
        return 'medium'
    return cleaned


def _normalize_note_type(value: str | None) -> str:
    cleaned = str(value or 'general').strip().lower()
    if cleaned not in ALLOWED_NOTE_TYPES:
        return 'general'
    return cleaned


def _derive_due_state(note: dict[str, Any], now: datetime | None = None) -> str:
    if bool(note.get('completed', False)):
        return 'completed'
    due_at = _parse_dt(note.get('due_at'))
    if not due_at:
        return 'none'
    now_dt = now or _utc_now_dt()
    if due_at < now_dt:
        return 'overdue'
    if due_at.date() == now_dt.date():
        return 'today'
    return 'upcoming'


def _read_notes() -> list[dict[str, Any]]:
    payload = read_json_file(NOTES_PATH, [])
    if isinstance(payload, list):
        normalized: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            note = dict(item)
            note['ticker'] = _normalize_ticker(note.get('ticker'))
            note['tags'] = _normalize_tags(note.get('tags'))
            note['owner'] = _normalize_owner(note.get('owner'))
            note['priority'] = _normalize_priority(note.get('priority'))
            note['note_type'] = _normalize_note_type(note.get('note_type'))
            note['recurrence'] = _normalize_recurrence(note.get('recurrence'))
            note['recurrence_end_at'] = _normalize_due_at(note.get('recurrence_end_at'))
            note['source_url'] = _normalize_source_url(note.get('source_url'))
            note['checklist'] = _normalize_checklist(note.get('checklist'))
            note['related_note_ids'] = _normalize_note_id_list(note.get('related_note_ids'))
            note['blocked_by_ids'] = _normalize_note_id_list(note.get('blocked_by_ids'))
            note['pinned'] = bool(note.get('pinned', False))
            note['archived'] = bool(note.get('archived', False))
            note['completed'] = bool(note.get('completed', False))
            note['estimate_minutes'] = _normalize_minutes(note.get('estimate_minutes'))
            note['spent_minutes'] = _normalize_minutes(note.get('spent_minutes'))
            note['due_at'] = _normalize_due_at(note.get('due_at'))
            note['reminder_at'] = _normalize_reminder_at(note.get('reminder_at'))
            normalized.append(note)
        return normalized
    return []


def _write_notes(notes: list[dict[str, Any]]) -> None:
    write_json_file(NOTES_PATH, notes)


def _sort_notes(notes: list[dict[str, Any]], sort_by: str) -> list[dict[str, Any]]:
    cleaned_sort = str(sort_by or 'updated_desc').strip().lower()
    if cleaned_sort not in ALLOWED_SORTS:
        cleaned_sort = 'updated_desc'

    priority_rank = {'high': 0, 'medium': 1, 'low': 2}

    if cleaned_sort == 'updated_asc':
        ordered = sorted(notes, key=lambda item: str(item.get('updated_at', '')))
    elif cleaned_sort == 'created_desc':
        ordered = sorted(notes, key=lambda item: str(item.get('created_at', '')), reverse=True)
    elif cleaned_sort == 'created_asc':
        ordered = sorted(notes, key=lambda item: str(item.get('created_at', '')))
    elif cleaned_sort == 'priority':
        ordered = sorted(
            notes,
            key=lambda item: (
                priority_rank.get(str(item.get('priority', 'medium')).lower(), 1),
                bool(item.get('completed', False)),
                not bool(item.get('pinned', False)),
                str(item.get('updated_at', '')),
            ),
        )
    elif cleaned_sort == 'title':
        ordered = sorted(notes, key=lambda item: str(item.get('title', '')).lower())
    elif cleaned_sort == 'due_asc':
        ordered = sorted(notes, key=lambda item: (_parse_dt(item.get('due_at')) is None, _parse_dt(item.get('due_at')) or datetime.max.replace(tzinfo=timezone.utc)))
    elif cleaned_sort == 'due_desc':
        ordered = sorted(notes, key=lambda item: (_parse_dt(item.get('due_at')) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    else:
        ordered = sorted(notes, key=lambda item: str(item.get('updated_at', '')), reverse=True)

    return sorted(ordered, key=lambda item: (bool(item.get('completed', False)), not bool(item.get('pinned', False))))


def list_notes(
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
) -> dict[str, Any]:
    notes = _read_notes()
    needle = str(search or '').strip().lower()
    ticker_filter = _normalize_ticker(ticker)
    status_filter = str(status or 'all').strip().lower()
    tag_filter = str(tag or '').strip().lower()
    priority_filter = str(priority or 'all').strip().lower()
    type_filter = str(note_type or 'all').strip().lower()
    due_filter = str(due_state or 'all').strip().lower()
    completed_filter = str(completed or 'all').strip().lower()
    owner_filter = _normalize_owner(owner).lower()
    has_link_filter = str(has_link or 'all').strip().lower()
    checklist_filter = str(checklist_state or 'all').strip().lower()
    reminder_filter = str(reminder_state or 'all').strip().lower()
    recurrence_filter = str(recurrence or 'all').strip().lower()
    blocked_filter = str(blocked_state or 'all').strip().lower()
    progress_filter = str(progress_state or 'all').strip().lower()
    now_dt = _utc_now_dt()
    note_ids = {str(item.get('id') or '').strip() for item in notes if str(item.get('id') or '').strip()}

    visible: list[dict[str, Any]] = []
    for note in notes:
        archived = bool(note.get('archived', False))
        note_completed = bool(note.get('completed', False))
        if status_filter == 'active' and archived:
            continue
        if status_filter == 'archived' and not archived:
            continue
        if ticker_filter and str(note.get('ticker', '')).upper() != ticker_filter:
            continue
        if pinned_only and not bool(note.get('pinned', False)):
            continue
        if priority_filter != 'all' and str(note.get('priority', 'medium')).lower() != priority_filter:
            continue
        if type_filter != 'all' and str(note.get('note_type', 'general')).lower() != type_filter:
            continue
        if completed_filter == 'open' and note_completed:
            continue
        if completed_filter == 'completed' and not note_completed:
            continue
        if owner_filter and str(note.get('owner', '')).lower() != owner_filter:
            continue
        derived_due = _derive_due_state(note, now=now_dt)
        if due_filter != 'all' and derived_due != due_filter:
            continue
        has_source_url = bool(str(note.get('source_url') or '').strip())
        if has_link_filter == 'yes' and not has_source_url:
            continue
        if has_link_filter == 'no' and has_source_url:
            continue
        checklist_progress = _checklist_progress(note)
        if checklist_filter == 'none' and checklist_progress['total'] > 0:
            continue
        if checklist_filter == 'open' and checklist_progress['open'] <= 0:
            continue
        if checklist_filter == 'done' and not (checklist_progress['total'] > 0 and checklist_progress['open'] == 0):
            continue
        reminder_at = _parse_dt(note.get('reminder_at'))
        note_recurrence = _normalize_recurrence(note.get('recurrence'))
        note_blocked_state = _derive_blocked_state(note, note_ids=note_ids)
        note_progress_state = _derive_progress_state(note)
        if reminder_filter == 'none' and reminder_at is not None:
            continue
        if reminder_filter == 'scheduled' and reminder_at is None:
            continue
        if reminder_filter == 'today' and not (reminder_at and reminder_at.date() == now_dt.date()):
            continue
        if reminder_filter == 'due' and not (reminder_at and reminder_at <= now_dt):
            continue
        if reminder_filter == 'upcoming' and not (reminder_at and reminder_at > now_dt):
            continue
        if recurrence_filter != 'all' and note_recurrence != recurrence_filter:
            continue
        if blocked_filter != 'all' and note_blocked_state != blocked_filter:
            continue
        if progress_filter != 'all' and note_progress_state != progress_filter:
            continue
        if tag_filter and tag_filter not in [str(item).lower() for item in note.get('tags', [])]:
            continue
        haystack = ' '.join([
            str(note.get('title', '')),
            str(note.get('body', '')),
            str(note.get('ticker', '')),
            ' '.join([str(item) for item in note.get('tags', [])]),
            str(note.get('priority', 'medium')),
            str(note.get('note_type', 'general')),
            str(note.get('owner', '')),
        ]).lower()
        if needle and needle not in haystack:
            continue
        enriched = dict(note)
        enriched['due_state'] = derived_due
        enriched['checklist_progress'] = checklist_progress
        enriched['reminder_due'] = bool(reminder_at and reminder_at <= now_dt)
        enriched['recurrence'] = note_recurrence
        enriched['blocked_state'] = note_blocked_state
        enriched['progress_state'] = note_progress_state
        enriched['progress_percent'] = _derive_progress_percent(note)
        enriched['estimate_minutes'] = _normalize_minutes(note.get('estimate_minutes'))
        enriched['spent_minutes'] = _normalize_minutes(note.get('spent_minutes'))
        visible.append(enriched)

    visible = _sort_notes(visible, sort_by)

    tags: dict[str, int] = {}
    ticker_counts: dict[str, int] = {}
    owner_counts: dict[str, int] = {}
    priority_counts = {'low': 0, 'medium': 0, 'high': 0}
    type_counts: dict[str, int] = {key: 0 for key in sorted(ALLOWED_NOTE_TYPES)}
    due_counts = {'none': 0, 'upcoming': 0, 'today': 0, 'overdue': 0, 'completed': 0}
    linked_count = 0
    checklist_item_count = 0
    checklist_open_count = 0
    reminder_count = 0
    reminder_due_count = 0
    reminder_today_count = 0
    recurring_count = 0
    reminder_next_24h_count = 0
    blocked_count = 0
    ready_count = 0
    orphan_dependency_count = 0
    total_estimate_minutes = 0
    total_spent_minutes = 0
    progress_counts = {'not_started': 0, 'planned': 0, 'in_progress': 0, 'done': 0}
    for item in notes:
        ticker_value = str(item.get('ticker', '')).upper()
        if ticker_value:
            ticker_counts[ticker_value] = ticker_counts.get(ticker_value, 0) + 1
        owner_value = _normalize_owner(item.get('owner'))
        if owner_value:
            owner_counts[owner_value] = owner_counts.get(owner_value, 0) + 1
        priority_value = _normalize_priority(item.get('priority'))
        priority_counts[priority_value] = priority_counts.get(priority_value, 0) + 1
        note_type_value = _normalize_note_type(item.get('note_type'))
        type_counts[note_type_value] = type_counts.get(note_type_value, 0) + 1
        if str(item.get('source_url') or '').strip():
            linked_count += 1
        progress = _checklist_progress(item)
        checklist_item_count += progress['total']
        checklist_open_count += progress['open']
        reminder_at = _parse_dt(item.get('reminder_at'))
        if _normalize_recurrence(item.get('recurrence')) != 'none':
            recurring_count += 1
        item_blocked_state = _derive_blocked_state(item, note_ids=note_ids)
        item_progress_state = _derive_progress_state(item)
        progress_counts[item_progress_state] = progress_counts.get(item_progress_state, 0) + 1
        total_estimate_minutes += _normalize_minutes(item.get('estimate_minutes'))
        total_spent_minutes += _normalize_minutes(item.get('spent_minutes'))
        if item_blocked_state == 'blocked':
            blocked_count += 1
        else:
            ready_count += 1
        orphan_dependency_count += sum(1 for blocker in _normalize_note_id_list(item.get('blocked_by_ids')) if blocker not in note_ids)
        if reminder_at:
            reminder_count += 1
            if reminder_at <= now_dt:
                reminder_due_count += 1
            if reminder_at.date() == now_dt.date():
                reminder_today_count += 1
            if reminder_at > now_dt and reminder_at <= now_dt + timedelta(hours=24):
                reminder_next_24h_count += 1
        due_counts[_derive_due_state(item, now=now_dt)] = due_counts.get(_derive_due_state(item, now=now_dt), 0) + 1
        for raw_tag in item.get('tags', []):
            cleaned = str(raw_tag).strip().lower()
            if cleaned:
                tags[cleaned] = tags.get(cleaned, 0) + 1

    limited = visible[: max(1, int(limit))]
    return {
        'items': limited,
        'count': len(limited),
        'total': len(visible),
        'active_count': sum(1 for item in notes if not item.get('archived', False)),
        'archived_count': sum(1 for item in notes if item.get('archived', False)),
        'pinned_count': sum(1 for item in notes if item.get('pinned', False)),
        'completed_count': sum(1 for item in notes if item.get('completed', False)),
        'open_count': sum(1 for item in notes if not item.get('completed', False) and not item.get('archived', False)),
        'high_priority_count': sum(1 for item in notes if str(item.get('priority', 'medium')).lower() == 'high' and not item.get('archived', False)),
        'overdue_count': sum(1 for item in notes if _derive_due_state(item, now=now_dt) == 'overdue'),
        'today_count': sum(1 for item in notes if _derive_due_state(item, now=now_dt) == 'today'),
        'tickers': [{'ticker': key, 'count': value} for key, value in sorted(ticker_counts.items())],
        'owners': [{'owner': key, 'count': value} for key, value in sorted(owner_counts.items())],
        'tags': [{'tag': key, 'count': value} for key, value in sorted(tags.items())],
        'priority_counts': priority_counts,
        'type_counts': type_counts,
        'due_counts': due_counts,
        'linked_count': linked_count,
        'checklist_item_count': checklist_item_count,
        'checklist_open_count': checklist_open_count,
        'reminder_count': reminder_count,
        'reminder_due_count': reminder_due_count,
        'reminder_today_count': reminder_today_count,
        'recurring_count': recurring_count,
        'reminder_next_24h_count': reminder_next_24h_count,
        'blocked_count': blocked_count,
        'ready_count': ready_count,
        'orphan_dependency_count': orphan_dependency_count,
        'total_estimate_minutes': total_estimate_minutes,
        'total_spent_minutes': total_spent_minutes,
        'progress_counts': progress_counts,
        'blocked_state': blocked_state,
        'progress_state': progress_state,
        'sort_by': sort_by,
    }


def get_notes_summary() -> dict[str, Any]:
    payload = list_notes(status='all', limit=500, sort_by='priority')
    items = payload.get('items', [])
    review_loop_items = [
        item
        for item in items
        if 'review-loop' in [str(tag).strip().lower() for tag in item.get('tags', [])]
        and not bool(item.get('archived', False))
    ]
    review_loop_open = [item for item in review_loop_items if not bool(item.get('completed', False))]
    review_loop_resolved = sorted(
        [item for item in review_loop_items if bool(item.get('completed', False))],
        key=lambda item: str(item.get('updated_at') or ''),
        reverse=True,
    )
    return {
        'total': payload.get('active_count', 0) + payload.get('archived_count', 0),
        'active_count': payload.get('active_count', 0),
        'archived_count': payload.get('archived_count', 0),
        'pinned_count': payload.get('pinned_count', 0),
        'high_priority_count': payload.get('high_priority_count', 0),
        'completed_count': payload.get('completed_count', 0),
        'open_count': payload.get('open_count', 0),
        'overdue_count': payload.get('overdue_count', 0),
        'today_count': payload.get('today_count', 0),
        'due_soon_count': sum(1 for item in items if item.get('due_state') in {'today', 'upcoming'} and not item.get('completed', False)),
        'linked_count': payload.get('linked_count', 0),
        'checklist_item_count': payload.get('checklist_item_count', 0),
        'checklist_open_count': payload.get('checklist_open_count', 0),
        'reminder_count': payload.get('reminder_count', 0),
        'reminder_due_count': payload.get('reminder_due_count', 0),
        'reminder_today_count': payload.get('reminder_today_count', 0),
        'recurring_count': payload.get('recurring_count', 0),
        'reminder_next_24h_count': payload.get('reminder_next_24h_count', 0),
        'top_tags': payload.get('tags', [])[:6],
        'top_owners': payload.get('owners', [])[:6],
        'top_tickers': payload.get('tickers', [])[:6],
        'review_loop_summary': {
            'open_count': len(review_loop_open),
            'resolved_count': len(review_loop_resolved),
            'latest_resolved': review_loop_resolved[0] if review_loop_resolved else None,
        },
        'recent': items[:5],
    }


def create_note(
    title: str,
    body: str = '',
    ticker: str = '',
    tags: list[str] | None = None,
    owner: str = '',
    source_url: str = '',
    checklist: list[dict[str, Any]] | None = None,
    related_note_ids: list[str] | None = None,
    blocked_by_ids: list[str] | None = None,
    pinned: bool = False,
    priority: str = 'medium',
    note_type: str = 'general',
    due_at: str | None = None,
    reminder_at: str | None = None,
    recurrence: str = 'none',
    recurrence_end_at: str | None = None,
    completed: bool = False,
    estimate_minutes: int = 0,
    spent_minutes: int = 0,
) -> dict[str, Any]:
    notes = _read_notes()
    now = _utc_now()
    note = {
        'id': uuid4().hex[:12],
        'title': str(title or '').strip(),
        'body': str(body or '').strip(),
        'ticker': _normalize_ticker(ticker),
        'tags': _normalize_tags(tags),
        'owner': _normalize_owner(owner),
        'source_url': _normalize_source_url(source_url),
        'checklist': _normalize_checklist(checklist),
        'related_note_ids': _normalize_note_id_list(related_note_ids),
        'blocked_by_ids': _normalize_note_id_list(blocked_by_ids),
        'priority': _normalize_priority(priority),
        'note_type': _normalize_note_type(note_type),
        'due_at': _normalize_due_at(due_at),
        'reminder_at': _normalize_reminder_at(reminder_at),
        'recurrence': _normalize_recurrence(recurrence),
        'recurrence_end_at': _normalize_due_at(recurrence_end_at),
        'completed': bool(completed),
        'estimate_minutes': _normalize_minutes(estimate_minutes),
        'spent_minutes': _normalize_minutes(spent_minutes),
        'pinned': bool(pinned),
        'archived': False,
        'created_at': now,
        'updated_at': now,
    }
    notes.insert(0, note)
    _write_notes(notes)
    return note


def update_note(note_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    notes = _read_notes()
    for note in notes:
        if str(note.get('id')) == str(note_id):
            if 'title' in payload and payload.get('title') is not None:
                note['title'] = str(payload.get('title') or '').strip()
            if 'body' in payload and payload.get('body') is not None:
                note['body'] = str(payload.get('body') or '').strip()
            if 'ticker' in payload and payload.get('ticker') is not None:
                note['ticker'] = _normalize_ticker(payload.get('ticker'))
            if 'tags' in payload and payload.get('tags') is not None:
                note['tags'] = _normalize_tags(payload.get('tags'))
            if 'owner' in payload and payload.get('owner') is not None:
                note['owner'] = _normalize_owner(payload.get('owner'))
            if 'source_url' in payload and payload.get('source_url') is not None:
                note['source_url'] = _normalize_source_url(payload.get('source_url'))
            if 'checklist' in payload and payload.get('checklist') is not None:
                note['checklist'] = _normalize_checklist(payload.get('checklist'))
            if 'related_note_ids' in payload and payload.get('related_note_ids') is not None:
                note['related_note_ids'] = _normalize_note_id_list(payload.get('related_note_ids'))
            if 'blocked_by_ids' in payload and payload.get('blocked_by_ids') is not None:
                note['blocked_by_ids'] = _normalize_note_id_list(payload.get('blocked_by_ids'))
            if 'priority' in payload and payload.get('priority') is not None:
                note['priority'] = _normalize_priority(payload.get('priority'))
            if 'note_type' in payload and payload.get('note_type') is not None:
                note['note_type'] = _normalize_note_type(payload.get('note_type'))
            if 'due_at' in payload:
                note['due_at'] = _normalize_due_at(payload.get('due_at'))
            if 'reminder_at' in payload:
                note['reminder_at'] = _normalize_reminder_at(payload.get('reminder_at'))
            if 'completed' in payload and payload.get('completed') is not None:
                note['completed'] = bool(payload.get('completed'))
            if 'estimate_minutes' in payload and payload.get('estimate_minutes') is not None:
                note['estimate_minutes'] = _normalize_minutes(payload.get('estimate_minutes'))
            if 'spent_minutes' in payload and payload.get('spent_minutes') is not None:
                note['spent_minutes'] = _normalize_minutes(payload.get('spent_minutes'))
            if 'recurrence' in payload and payload.get('recurrence') is not None:
                note['recurrence'] = _normalize_recurrence(payload.get('recurrence'))
            if 'recurrence_end_at' in payload:
                note['recurrence_end_at'] = _normalize_due_at(payload.get('recurrence_end_at'))
            if 'pinned' in payload and payload.get('pinned') is not None:
                note['pinned'] = bool(payload.get('pinned'))
            if 'archived' in payload and payload.get('archived') is not None:
                note['archived'] = bool(payload.get('archived'))
            note['updated_at'] = _utc_now()
            _write_notes(notes)
            enriched = dict(note)
            enriched['due_state'] = _derive_due_state(note)
            enriched['checklist_progress'] = _checklist_progress(note)
            return enriched
    raise NotFoundError('Note not found.')


def delete_note(note_id: str) -> dict[str, Any]:
    notes = _read_notes()
    kept = [note for note in notes if str(note.get('id')) != str(note_id)]
    deleted = len(kept) != len(notes)
    _write_notes(kept)
    return {'deleted': deleted, 'count': len(kept)}


def duplicate_note(note_id: str) -> dict[str, Any]:
    notes = _read_notes()
    for note in notes:
        if str(note.get('id')) == str(note_id):
            return create_note(
                title=f"{str(note.get('title', 'Untitled')).strip()} (Copy)",
                body=str(note.get('body', '')),
                ticker=str(note.get('ticker', '')),
                tags=list(note.get('tags', [])),
                owner=str(note.get('owner', '')),
                source_url=str(note.get('source_url', '')),
                checklist=list(note.get('checklist', [])),
                related_note_ids=list(note.get('related_note_ids', [])),
                blocked_by_ids=list(note.get('blocked_by_ids', [])),
                pinned=False,
                priority=str(note.get('priority', 'medium')),
                note_type=str(note.get('note_type', 'general')),
                due_at=note.get('due_at'),
                reminder_at=note.get('reminder_at'),
                recurrence=note.get('recurrence', 'none'),
                recurrence_end_at=note.get('recurrence_end_at'),
            )
    raise NotFoundError('Note not found.')


def export_notes() -> dict[str, Any]:
    notes = _read_notes()
    return {'items': notes, 'count': len(notes), 'exported_at': _utc_now()}


def import_notes(items: list[dict[str, Any]], mode: str = 'merge') -> dict[str, Any]:
    existing = [] if str(mode or 'merge').lower() == 'replace' else _read_notes()
    existing_map = {str(item.get('id')): item for item in existing if str(item.get('id'))}
    imported = 0
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        note_id = str(raw.get('id') or uuid4().hex[:12])
        normalized = {
            'id': note_id,
            'title': str(raw.get('title') or '').strip()[:120],
            'body': str(raw.get('body') or '').strip()[:4000],
            'ticker': _normalize_ticker(raw.get('ticker')),
            'tags': _normalize_tags(raw.get('tags')),
            'owner': _normalize_owner(raw.get('owner')),
            'source_url': _normalize_source_url(raw.get('source_url')),
            'checklist': _normalize_checklist(raw.get('checklist')),
            'related_note_ids': _normalize_note_id_list(raw.get('related_note_ids')),
            'blocked_by_ids': _normalize_note_id_list(raw.get('blocked_by_ids')),
            'priority': _normalize_priority(raw.get('priority')),
            'note_type': _normalize_note_type(raw.get('note_type')),
            'due_at': _normalize_due_at(raw.get('due_at')),
            'reminder_at': _normalize_reminder_at(raw.get('reminder_at')),
            'recurrence': _normalize_recurrence(raw.get('recurrence')),
            'recurrence_end_at': _normalize_due_at(raw.get('recurrence_end_at')),
            'completed': bool(raw.get('completed', False)),
            'pinned': bool(raw.get('pinned', False)),
            'archived': bool(raw.get('archived', False)),
            'created_at': str(raw.get('created_at') or _utc_now()),
            'updated_at': str(raw.get('updated_at') or _utc_now()),
        }
        existing_map[note_id] = normalized
        imported += 1
    merged = list(existing_map.values())
    merged.sort(key=lambda item: str(item.get('updated_at', '')), reverse=True)
    _write_notes(merged)
    return {'imported': imported, 'count': len(merged), 'mode': mode}


def advance_note(note_id: str) -> dict[str, Any]:
    notes = _read_notes()
    for note in notes:
        if str(note.get('id')) == str(note_id):
            recurrence = _normalize_recurrence(note.get('recurrence'))
            if recurrence == 'none':
                raise ValidationError('Note is not recurring.')
            _advance_note_schedule(note)
            note['updated_at'] = _utc_now()
            _write_notes(notes)
            enriched = dict(note)
            enriched['due_state'] = _derive_due_state(note)
            enriched['checklist_progress'] = _checklist_progress(note)
            return enriched
    raise NotFoundError('Note not found.')


def get_notes_agenda(days: int = 7, status: str = 'active') -> dict[str, Any]:
    days_value = max(1, min(int(days), 30))
    now_dt = _utc_now_dt()
    end_dt = now_dt + timedelta(days=days_value)
    notes = _read_notes()
    note_ids = {str(item.get('id') or '').strip() for item in notes if str(item.get('id') or '').strip()}
    items: list[dict[str, Any]] = []
    for note in notes:
        if str(status or 'active').lower() == 'active' and bool(note.get('archived', False)):
            continue
        due_at = _parse_dt(note.get('due_at'))
        reminder_at = _parse_dt(note.get('reminder_at'))
        note_recurrence = _normalize_recurrence(note.get('recurrence'))
        note_blocked_state = _derive_blocked_state(note, note_ids=note_ids)
        note_progress_state = _derive_progress_state(note)
        if due_at and now_dt <= due_at <= end_dt:
            item = dict(note)
            item['agenda_kind'] = 'due'
            item['agenda_at'] = due_at.isoformat()
            item['recurrence'] = note_recurrence
            item['blocked_state'] = note_blocked_state
            item['progress_state'] = note_progress_state
            items.append(item)
        if reminder_at and now_dt <= reminder_at <= end_dt:
            item = dict(note)
            item['agenda_kind'] = 'reminder'
            item['agenda_at'] = reminder_at.isoformat()
            item['recurrence'] = note_recurrence
            item['blocked_state'] = note_blocked_state
            item['progress_state'] = note_progress_state
            items.append(item)
    items.sort(key=lambda item: str(item.get('agenda_at') or ''))
    return {'items': items[:100], 'count': min(len(items), 100), 'days': days_value}



def get_notes_board(status: str = 'active') -> dict[str, Any]:
    payload = list_notes(status=status, limit=500, sort_by='priority')
    items = payload.get('items', [])
    now_dt = _utc_now_dt()

    def _serialize(board_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return board_items[:8]

    blocked_items = [item for item in items if item.get('blocked_state') == 'blocked' and not item.get('completed', False)]
    ready_items = [item for item in items if item.get('blocked_state') == 'ready' and not item.get('completed', False) and not item.get('archived', False)]
    urgent_items = [item for item in items if item.get('due_state') in {'today', 'overdue'} and not item.get('completed', False)]
    reminder_items = [item for item in items if item.get('reminder_at') and (_parse_dt(item.get('reminder_at')) or now_dt) <= now_dt + timedelta(hours=24) and not item.get('completed', False)]
    high_priority_items = [item for item in items if str(item.get('priority', 'medium')).lower() == 'high' and not item.get('archived', False)]

    return {
        'columns': [
            {'key': 'blocked', 'label': 'Blocked', 'count': len(blocked_items), 'items': _serialize(blocked_items)},
            {'key': 'ready', 'label': 'Ready', 'count': len(ready_items), 'items': _serialize(ready_items)},
            {'key': 'urgent', 'label': 'Urgent', 'count': len(urgent_items), 'items': _serialize(urgent_items)},
            {'key': 'reminders', 'label': 'Reminders', 'count': len(reminder_items), 'items': _serialize(reminder_items)},
            {'key': 'high_priority', 'label': 'High priority', 'count': len(high_priority_items), 'items': _serialize(high_priority_items)},
        ],
        'status': status,
        'generated_at': _utc_now(),
    }







def get_recent_notes(limit: int = 8, include_archived: bool = False) -> dict[str, Any]:
    payload = list_notes(status='all' if include_archived else 'active', limit=max(1, min(int(limit), 50)), sort_by='updated_desc')
    return {
        'items': payload.get('items', [])[: max(1, min(int(limit), 50))],
        'count': min(payload.get('count', 0), max(1, min(int(limit), 50))),
        'total': payload.get('total', 0),
    }



def get_notes_calendar(days: int = 14, status: str = 'active') -> dict[str, Any]:
    days_value = max(1, min(int(days), 60))
    now_dt = _utc_now_dt()
    end_dt = now_dt + timedelta(days=days_value)
    notes = _read_notes()
    groups: dict[str, dict[str, Any]] = {}
    for note in notes:
        if str(status or 'active').lower() == 'active' and bool(note.get('archived', False)):
            continue
        due_at = _parse_dt(note.get('due_at'))
        if not due_at:
            continue
        if due_at < now_dt or due_at > end_dt:
            continue
        key = due_at.date().isoformat()
        group = groups.setdefault(key, {'date': key, 'items': []})
        item = dict(note)
        item['due_state'] = _derive_due_state(note, now=now_dt)
        item['checklist_progress'] = _checklist_progress(note)
        group['items'].append(item)
    ordered = []
    for key in sorted(groups.keys()):
        group = groups[key]
        group['items'].sort(key=lambda item: (str(item.get('due_at') or ''), not bool(item.get('pinned', False))))
        group['count'] = len(group['items'])
        ordered.append(group)
    return {'days': days_value, 'groups': ordered, 'count': sum(group['count'] for group in ordered)}


def bulk_update_notes(note_ids: list[str], action: str) -> dict[str, Any]:
    action_clean = str(action or '').strip().lower()
    allowed = {'complete', 'reopen', 'archive', 'restore', 'delete', 'pin', 'unpin'}
    if action_clean not in allowed:
        raise ValidationError('Unsupported note bulk action.')
    ids = {str(item).strip() for item in note_ids if str(item).strip()}
    if not ids:
        raise ValidationError('At least one note id is required.')

    notes = _read_notes()
    updated = 0
    kept: list[dict[str, Any]] = []
    now = _utc_now()
    for note in notes:
        if str(note.get('id')) not in ids:
            kept.append(note)
            continue
        if action_clean == 'delete':
            updated += 1
            continue
        if action_clean == 'complete':
            if _normalize_recurrence(note.get('recurrence')) != 'none':
                _advance_note_schedule(note)
            else:
                note['completed'] = True
        elif action_clean == 'reopen':
            note['completed'] = False
        elif action_clean == 'archive':
            note['archived'] = True
        elif action_clean == 'restore':
            note['archived'] = False
        elif action_clean == 'pin':
            note['pinned'] = True
        elif action_clean == 'unpin':
            note['pinned'] = False
        note['updated_at'] = now
        kept.append(note)
        updated += 1
    _write_notes(kept)
    return {'action': action_clean, 'updated': updated, 'remaining_count': len(kept)}


def snooze_note(note_id: str, minutes: int) -> dict[str, Any]:
    minutes_value = max(1, min(int(minutes), 43200))
    reminder_at = (_utc_now_dt() + timedelta(minutes=minutes_value)).isoformat()
    return update_note(note_id, {'reminder_at': reminder_at, 'completed': False, 'archived': False})
