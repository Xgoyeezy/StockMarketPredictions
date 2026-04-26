from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence
from institutional_trading.interfaces import MarketDataProvider
from institutional_trading.models import DataGranularity, HealthStatus, MarketRecord, ServiceHealth
@dataclass
class StaticMarketDataProvider:
    records: list[MarketRecord]; provider_name: str = "static"; fail: bool = False
    def historical(self, *, symbol: str, start: datetime, end: datetime, granularity: DataGranularity, include_extended_hours: bool) -> Sequence[MarketRecord]:
        if self.fail: raise RuntimeError(f"{self.provider_name} provider unavailable")
        return [r for r in self.records if r.symbol == symbol.upper() and start <= r.timestamp <= end and r.granularity == granularity and (include_extended_hours or r.session.value == "regular")]
    def latest(self, *, symbol: str, granularity: DataGranularity) -> Sequence[MarketRecord]:
        if self.fail: raise RuntimeError(f"{self.provider_name} provider unavailable")
        rows = [r for r in self.records if r.symbol == symbol.upper() and r.granularity == granularity]; return rows[-1:] if rows else []
    def health(self) -> ServiceHealth:
        status = HealthStatus.FAILED if self.fail else HealthStatus.HEALTHY; return ServiceHealth(status, f"market_data.{self.provider_name}", status.value)
@dataclass
class FailoverMarketDataProvider:
    providers: list[MarketDataProvider]; provider_name: str = "failover"; failure_log: list[str] = field(default_factory=list)
    def historical(self, **kwargs) -> Sequence[MarketRecord]:
        for p in self.providers:
            try:
                rows = p.historical(**kwargs)
                if rows: return rows
            except Exception as exc: self.failure_log.append(f"{p.provider_name}: {exc}")
        return []
    def latest(self, **kwargs) -> Sequence[MarketRecord]:
        for p in self.providers:
            try:
                rows = p.latest(**kwargs)
                if rows: return rows
            except Exception as exc: self.failure_log.append(f"{p.provider_name}: {exc}")
        return []
    def health(self) -> ServiceHealth:
        if not self.providers: return ServiceHealth(HealthStatus.FAILED, "market_data.failover", "No providers configured.")
        healthy = [p.health().healthy for p in self.providers]
        if any(healthy): return ServiceHealth(HealthStatus.DEGRADED if self.failure_log else HealthStatus.HEALTHY, "market_data.failover", "At least one provider is available.", {"failures": len(self.failure_log)})
        return ServiceHealth(HealthStatus.FAILED, "market_data.failover", "All providers failed.", {"failures": len(self.failure_log)})
