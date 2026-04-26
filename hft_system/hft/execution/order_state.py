from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrderLifecycleState(str, Enum):
    CREATED = "CREATED"
    SENT = "SENT"
    ACKED = "ACKED"
    LIVE = "LIVE"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class SimulatedOrder:
    order_id: str
    symbol: str
    strategy_name: str
    side: str
    price: float
    quantity: float
    decision_timestamp: int
    send_timestamp: int
    exchange_receive_timestamp: int
    ack_timestamp: int
    fill_timestamp: int | None = None
    quote_width: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    state: OrderLifecycleState = OrderLifecycleState.CREATED
    filled_quantity: float = 0.0

    @property
    def remaining_quantity(self) -> float:
        return max(self.quantity - self.filled_quantity, 0.0)


@dataclass(frozen=True)
class SimulatedFill:
    order_id: str
    symbol: str
    strategy_name: str
    side: str
    price: float
    quantity: float
    fill_timestamp: int
    liquidity_flag: str = "maker"
