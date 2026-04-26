from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def build_dashboard_payload(result: Any) -> dict[str, Any]:
    orders = list(getattr(result, "orders", []) or [])
    active_quotes = [
        _serialize(order)
        for order in orders
        if getattr(order, "state", "") in {"LIVE", "PARTIALLY_FILLED", "ACKED", "SENT"}
    ]
    return {
        "run_id": getattr(result, "run_id", ""),
        "symbol": getattr(result, "symbol", ""),
        "orders_submitted": len(orders),
        "fills": len(getattr(result, "fills", [])),
        "alerts": [alert.__dict__ for alert in getattr(result, "alerts", [])],
        "metrics": dict(getattr(result, "metrics", {})),
        "inventory": _serialize(getattr(result, "inventory_snapshot", None)) if getattr(result, "inventory_snapshot", None) else None,
        "strategy_health": _serialize(getattr(result, "strategy_health", None)) if getattr(result, "strategy_health", None) else None,
        "order_book_state": _serialize(getattr(result, "final_book_snapshot", None)) if getattr(result, "final_book_snapshot", None) else None,
        "active_quotes": active_quotes,
        "latency": dict(getattr(result, "latency_summary", {}) or {}),
        "kill_switch_status": {
            "risk_state": getattr(getattr(result, "strategy_health", None), "risk_state", "unknown"),
            "enabled": bool(getattr(getattr(result, "strategy_health", None), "enabled", False)),
        },
    }
