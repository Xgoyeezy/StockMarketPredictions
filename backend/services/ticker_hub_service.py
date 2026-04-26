from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import settings
from backend.services.exceptions import ValidationServiceError
from backend.services.storage_utils import read_json_file, write_json_file

_LEGACY_TICKER_HUB_FILE = Path(__file__).resolve().parent.parent / 'data' / 'ticker_hub.json'
_TICKER_HUB_FILE = Path(settings.storage_dir) / 'ticker_hub.json'
_DEFAULTS: dict[str, Any] = {"favorites": [], "recent": [], "updated_at": None}
_MAX_FAVORITES = 24
_MAX_RECENT = 18


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_store() -> None:
    _TICKER_HUB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _TICKER_HUB_FILE.exists():
        write_json_file(_TICKER_HUB_FILE, _DEFAULTS)


def _normalize_ticker(value: str) -> str:
    cleaned = str(value or '').strip().upper()
    if not cleaned or len(cleaned) > 8 or not cleaned.replace('-', '').isalnum():
        raise ValidationServiceError('A valid ticker is required.')
    return cleaned


def _normalize_list(values: list[str] | None, *, limit: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        try:
            cleaned = _normalize_ticker(value)
        except ValidationServiceError:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
        if len(normalized) >= limit:
            break
    return normalized


def _read_store() -> dict[str, Any]:
    source = _TICKER_HUB_FILE if _TICKER_HUB_FILE.exists() or not _LEGACY_TICKER_HUB_FILE.exists() else _LEGACY_TICKER_HUB_FILE
    data = read_json_file(source, dict(_DEFAULTS))
    if not isinstance(data, dict):
        data = dict(_DEFAULTS)
    return {
        'favorites': _normalize_list(data.get('favorites'), limit=_MAX_FAVORITES),
        'recent': _normalize_list(data.get('recent'), limit=_MAX_RECENT),
        'updated_at': str(data.get('updated_at') or _utc_now()),
    }


def _write_store(payload: dict[str, Any]) -> None:
    payload = {
        'favorites': _normalize_list(payload.get('favorites'), limit=_MAX_FAVORITES),
        'recent': _normalize_list(payload.get('recent'), limit=_MAX_RECENT),
        'updated_at': _utc_now(),
    }
    _ensure_store()
    write_json_file(_TICKER_HUB_FILE, payload)


def get_ticker_hub(limit_recent: int = 8) -> dict[str, Any]:
    store = _read_store()
    recent_limit = max(1, min(int(limit_recent), _MAX_RECENT))
    return {
        'favorites': store['favorites'],
        'recent': store['recent'][:recent_limit],
        'favorite_count': len(store['favorites']),
        'recent_count': len(store['recent']),
        'updated_at': store['updated_at'],
    }


def record_recent_ticker(ticker: str) -> dict[str, Any]:
    cleaned = _normalize_ticker(ticker)
    store = _read_store()
    recent = [item for item in store['recent'] if item != cleaned]
    recent.insert(0, cleaned)
    store['recent'] = recent[:_MAX_RECENT]
    _write_store(store)
    return get_ticker_hub()


def toggle_favorite_ticker(ticker: str) -> dict[str, Any]:
    cleaned = _normalize_ticker(ticker)
    store = _read_store()
    favorites = list(store['favorites'])
    if cleaned in favorites:
        favorites = [item for item in favorites if item != cleaned]
        favorited = False
    else:
        favorites.insert(0, cleaned)
        favorites = _normalize_list(favorites, limit=_MAX_FAVORITES)
        favorited = True
    store['favorites'] = favorites
    _write_store(store)
    payload = get_ticker_hub()
    payload['ticker'] = cleaned
    payload['favorited'] = favorited
    return payload


def clear_recent_tickers() -> dict[str, Any]:
    store = _read_store()
    store['recent'] = []
    _write_store(store)
    return get_ticker_hub()
