from __future__ import annotations
from dataclasses import dataclass
from institutional_trading.execution.broker import BrokerUnavailable
from institutional_trading.models import HealthStatus, OrderIntent, ServiceHealth
@dataclass
class IBKRBrokerAdapter:
    host: str = "127.0.0.1"; port: int = 7497; client_id: int = 11; enable_live_trading: bool = False; adapter_name: str = "ibkr"
    def connect(self) -> ServiceHealth:
        if not self.enable_live_trading: return ServiceHealth(HealthStatus.DEGRADED, "broker.ibkr", "IBKR live submission is disabled; use PaperBrokerAdapter for local runs.")
        try: __import__("ibapi")
        except Exception as exc: raise BrokerUnavailable("IBKR ibapi package is not installed or unavailable.") from exc
        return ServiceHealth(HealthStatus.HEALTHY, "broker.ibkr", f"IBKR API dependency available for {self.host}:{self.port}.")
    def health(self) -> ServiceHealth: return ServiceHealth(HealthStatus.DEGRADED if not self.enable_live_trading else HealthStatus.HEALTHY, "broker.ibkr", "IBKR adapter boundary configured; live trading remains gated by enable_live_trading.")
    def submit_order(self, intent: OrderIntent): raise BrokerUnavailable("IBKR live order submission is intentionally not enabled in the paper-safe core.")
    def cancel_order(self, broker_order_id: str, *, reason: str): raise BrokerUnavailable("IBKR live cancel submission is intentionally not enabled in the paper-safe core.")
    def list_open_orders(self) -> list[object]: return []
    def fills(self) -> list[object]: return []
