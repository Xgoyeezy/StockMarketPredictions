from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()
_WINDOWS_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2, 0.35)


def _resolve_file_lock(file_path: Path) -> threading.Lock:
    normalized_path = str(file_path.resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(normalized_path)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[normalized_path] = lock
        return lock


def atomic_write_text(file_path: Path, content: str, *, encoding: str = "utf-8") -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    file_lock = _resolve_file_lock(file_path)
    with file_lock:
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding=encoding,
                dir=file_path.parent,
                delete=False,
                newline="",
            ) as temp_file:
                temp_name = temp_file.name
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            for attempt, delay in enumerate((0.0, *_WINDOWS_REPLACE_RETRY_DELAYS)):
                try:
                    os.replace(temp_name, file_path)
                    temp_name = ""
                    break
                except PermissionError:
                    if attempt >= len(_WINDOWS_REPLACE_RETRY_DELAYS):
                        raise
                    time.sleep(delay or _WINDOWS_REPLACE_RETRY_DELAYS[attempt])
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)


def read_json_file(file_path: Path, default: Any) -> Any:
    if not file_path.exists():
        return default
    try:
        return json.loads(file_path.read_text(encoding="utf-8") or "null")
    except (json.JSONDecodeError, OSError):
        return default


def write_json_file(file_path: Path, payload: Any) -> None:
    atomic_write_text(file_path, json.dumps(payload, indent=2), encoding="utf-8")


def write_dataframe_csv(file_path: Path, frame: Any) -> None:
    atomic_write_text(file_path, frame.to_csv(index=False), encoding="utf-8")
