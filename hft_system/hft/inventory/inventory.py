from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InventoryState:
    symbol: str
    position: float
    average_price: float
    realized_pnl: float
    unrealized_pnl: float
    inventory_age_ns: int
    inventory_skew: float
    hedge_requirement: float


@dataclass
class InventoryManager:
    symbol: str
    max_inventory: float
    position: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0
    last_fill_ts_ns: int = 0
    oldest_open_ts_ns: int = 0

    def update_from_fill(self, fill: Any) -> InventoryState:
        qty = float(getattr(fill, "quantity", 0.0))
        price = float(getattr(fill, "price", 0.0))
        side = str(getattr(fill, "side", "buy")).lower()
        timestamp_ns = int(getattr(fill, "fill_timestamp", getattr(fill, "timestamp_ns", 0)))
        signed_qty = qty if side == "buy" else -qty
        previous_position = self.position

        if previous_position == 0 or (previous_position > 0 and signed_qty > 0) or (previous_position < 0 and signed_qty < 0):
            new_position = previous_position + signed_qty
            if abs(new_position) > 0:
                carried_cost = self.average_price * abs(previous_position)
                new_cost = price * abs(signed_qty)
                self.average_price = (carried_cost + new_cost) / abs(new_position)
            self.position = new_position
            if self.oldest_open_ts_ns == 0:
                self.oldest_open_ts_ns = timestamp_ns
        else:
            closing_qty = min(abs(previous_position), abs(signed_qty))
            pnl_sign = 1.0 if previous_position > 0 else -1.0
            self.realized_pnl += pnl_sign * (price - self.average_price) * closing_qty
            self.position = previous_position + signed_qty
            if self.position == 0:
                self.average_price = 0.0
                self.oldest_open_ts_ns = 0
            elif previous_position > 0 > self.position or previous_position < 0 < self.position:
                self.average_price = price
                self.oldest_open_ts_ns = timestamp_ns

        self.last_fill_ts_ns = timestamp_ns
        return self.mark_to_market(price, timestamp_ns=timestamp_ns)

    def mark_to_market(self, mid_price: float, *, timestamp_ns: int) -> InventoryState:
        unrealized = 0.0
        if self.position != 0 and self.average_price:
            unrealized = (float(mid_price) - self.average_price) * self.position
        age_ns = max(0, int(timestamp_ns) - int(self.oldest_open_ts_ns)) if self.oldest_open_ts_ns else 0
        skew = (self.position / self.max_inventory) if self.max_inventory else 0.0
        return InventoryState(
            symbol=self.symbol,
            position=self.position,
            average_price=self.average_price,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            inventory_age_ns=age_ns,
            inventory_skew=skew,
            hedge_requirement=-self.position,
        )

    def forced_liquidation_quantity(self) -> float:
        return abs(self.position)

    def size_reduction_factor(self) -> float:
        if self.max_inventory <= 0:
            return 1.0
        utilization = min(abs(self.position) / self.max_inventory, 1.0)
        return max(0.0, 1.0 - utilization)

    def stop_quoting_side(self, *, hard_threshold: float = 0.8) -> str | None:
        if self.max_inventory <= 0:
            return None
        skew = self.position / self.max_inventory
        if skew >= hard_threshold:
            return "buy"
        if skew <= -hard_threshold:
            return "sell"
        return None

    def should_force_liquidate(self, *, hard_threshold: float = 0.95) -> bool:
        if self.max_inventory <= 0:
            return False
        return abs(self.position / self.max_inventory) >= hard_threshold
