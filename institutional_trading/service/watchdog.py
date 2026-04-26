from __future__ import annotations
from dataclasses import dataclass
from institutional_trading.models import HealthStatus, ServiceHealth
from institutional_trading.service.runner import TradingService
@dataclass
class Watchdog:
    service: TradingService; max_failed_checks: int = 3; failed_checks: int = 0
    def check_once(self) -> ServiceHealth:
        h = self.service.health.aggregate(); self.failed_checks = self.failed_checks + 1 if h.status == HealthStatus.FAILED else 0
        if self.failed_checks >= self.max_failed_checks: self.service.kill("watchdog_failed_health_checks")
        return h
