from __future__ import annotations

from pathlib import Path
from typing import Any

from institutional_trading.risk.limits import RiskLimits

DEFAULT_CONFIG_PATH = Path(__file__).with_name("example.yaml")


class ConfigError(ValueError):
    pass


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(f"Config file does not exist: {config_path}")
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError("PyYAML is required to read institutional_trading YAML config files.") from exc
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("Institutional trading config must be a mapping.")
    return loaded


def validate_paper_safe_config(config: dict[str, Any]) -> None:
    broker = _mapping(config.get("broker"), "broker")
    paper = _mapping(broker.get("paper", {}), "broker.paper")
    if bool(broker.get("live_trading_enabled", False)):
        raise ConfigError("Live trading is disabled for this paper-service enablement path.")
    if not bool(paper.get("enabled", False)):
        raise ConfigError("Paper broker must be enabled before the institutional service can start.")


def runtime_dir_from_config(config: dict[str, Any], override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    service = _mapping(config.get("service", {}), "service")
    return Path(service.get("runtime_dir", "runtime/institutional_trading"))


def audit_paths_from_config(config: dict[str, Any], runtime_dir: Path, runtime_overridden: bool = False) -> tuple[Path, Path | None]:
    audit = _mapping(config.get("audit", {}), "audit")
    if runtime_overridden:
        return runtime_dir / "audit" / "events.jsonl", runtime_dir / "audit" / "events.sqlite3"
    jsonl_path = Path(audit.get("jsonl_path", runtime_dir / "audit" / "events.jsonl"))
    sqlite_raw = audit.get("sqlite_index_path", runtime_dir / "audit" / "events.sqlite3")
    sqlite_path = Path(sqlite_raw) if sqlite_raw else None
    return jsonl_path, sqlite_path


def watchdog_interval_from_config(config: dict[str, Any], default: float = 2.0) -> float:
    watchdog = _mapping(_mapping(config.get("service", {}), "service").get("watchdog", {}), "service.watchdog")
    value = watchdog.get("heartbeat_interval_seconds", default)
    return max(float(value), 0.05)


def watchdog_max_failures_from_config(config: dict[str, Any], default: int = 3) -> int:
    watchdog = _mapping(_mapping(config.get("service", {}), "service").get("watchdog", {}), "service.watchdog")
    return max(int(watchdog.get("max_failed_checks", default)), 1)


def build_risk_limits(config: dict[str, Any]) -> RiskLimits:
    risk = _mapping(config.get("risk", {}), "risk")
    symbols = _mapping(config.get("symbols", {}), "symbols")
    restricted = set(symbols.get("restricted", []) or [])
    restricted.update(risk.get("restricted_symbols", []) or [])
    kwargs: dict[str, Any] = {}
    for key in (
        "max_order_quantity",
        "max_position_size",
        "max_symbol_exposure",
        "max_gross_exposure",
        "max_daily_loss",
        "max_drawdown",
    ):
        if key in risk:
            kwargs[key] = risk[key]
    kwargs["restricted_symbols"] = frozenset(str(s).upper() for s in restricted)
    return RiskLimits(**kwargs)


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config section {name!r} must be a mapping.")
    return value
