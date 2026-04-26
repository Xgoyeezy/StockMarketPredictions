from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from institutional_trading.models import FillReport, OrderIntent, OrderState, RiskDecision
class BrokerUnavailable(RuntimeError): pass
@dataclass
class OrderRecord:
    intent: OrderIntent; broker_order_id: str; state: OrderState = OrderState.CREATED; created_at: datetime | None = None; submitted_at: datetime | None = None; acknowledged_at: datetime | None = None; terminal_at: datetime | None = None; filled_quantity: int = 0; average_fill_price: float = 0.0; retry_count: int = 0; risk_decision: RiskDecision | None = None; state_history: list[tuple[OrderState, datetime, str]] = field(default_factory=list); fill_reports: list[FillReport] = field(default_factory=list)
    @property
    def remaining_quantity(self) -> int: return max(self.intent.quantity - self.filled_quantity, 0)
    @property
    def terminal(self) -> bool: return self.state in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.FAILED, OrderState.RISK_REJECTED}
