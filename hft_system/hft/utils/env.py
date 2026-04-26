from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cleaned = value.strip().strip("'").strip('"')
        values[key.strip()] = cleaned
    return values


def merged_env(path: str | Path | None = None) -> dict[str, str]:
    values = load_env_file(path)
    merged = dict(values)
    for key, value in os.environ.items():
        merged[key] = value
    return merged


def bool_from_value(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    cleaned = str(value).strip().lower()
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return default
