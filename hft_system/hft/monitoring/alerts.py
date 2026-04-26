from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AlertRecord:
    severity: str
    code: str
    detail: str
    metadata: dict[str, Any]


def evaluate_alerts(*, risk_reason: str | None, inventory_skew: float, realized_pnl: float, message_rate: float) -> list[AlertRecord]:
    alerts: list[AlertRecord] = []
    if risk_reason and risk_reason not in {"healthy", "allowed"}:
        alerts.append(AlertRecord("high", risk_reason, "Risk engine signaled an unhealthy state.", {}))
        if risk_reason == "stale_feed":
            alerts.append(AlertRecord("high", "data_stale", "The market-data feed became stale.", {}))
        if risk_reason in {"spread_too_wide", "volatility_spike"}:
            alerts.append(AlertRecord("medium", "quote_instability", "Quote stability degraded beyond safe limits.", {"reason": risk_reason}))
        if risk_reason == "disabled":
            alerts.append(AlertRecord("medium", "strategy_disabled", "Strategy is disabled by configuration.", {}))
    if abs(inventory_skew) >= 0.8:
        alerts.append(AlertRecord("high", "inventory_breach", "Inventory skew exceeded the warning threshold.", {"inventory_skew": inventory_skew}))
    if realized_pnl < 0 and abs(realized_pnl) >= 1_000:
        alerts.append(AlertRecord("medium", "drawdown_breach", "Replay drawdown breached the configured review threshold.", {"realized_pnl": realized_pnl}))
    if message_rate >= 100:
        alerts.append(AlertRecord("medium", "message_rate_breach", "Message rate is nearing or breaching cap.", {"message_rate": message_rate}))
    return alerts
