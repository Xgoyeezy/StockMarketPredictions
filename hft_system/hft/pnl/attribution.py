from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PnLAttributionSnapshot:
    symbol: str
    strategy_name: str
    realized_pnl: float
    unrealized_pnl: float
    spread_capture: float
    fees: float
    rebates: float
    slippage: float
    adverse_selection: float
    inventory_pnl: float
    missed_fills: int
    cancel_costs: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PnLAttributionEngine:
    symbol: str
    strategy_name: str
    maker_rebate_per_share: float = 0.0015
    taker_fee_per_share: float = 0.0030
    cancel_cost_per_order: float = 0.0001
    spread_capture: float = 0.0
    fees: float = 0.0
    rebates: float = 0.0
    slippage: float = 0.0
    adverse_selection: float = 0.0
    missed_fills: int = 0
    cancel_costs: float = 0.0

    def record_fill(self, fill: Any, *, mid_price_before: float, mid_price_after: float) -> None:
        qty = float(getattr(fill, "quantity", 0.0))
        price = float(getattr(fill, "price", 0.0))
        side = str(getattr(fill, "side", "buy")).lower()
        liquidity_flag = str(getattr(fill, "liquidity_flag", "maker")).lower()
        mid = float(mid_price_before)
        if side == "buy":
            self.spread_capture += max((mid - price) * qty, 0.0)
        else:
            self.spread_capture += max((price - mid) * qty, 0.0)
        if liquidity_flag == "maker":
            self.rebates += qty * self.maker_rebate_per_share
        else:
            self.fees += qty * self.taker_fee_per_share
        self.slippage += abs(price - mid) * qty
        signed_qty = qty if side == "buy" else -qty
        self.adverse_selection += (mid_price_after - price) * signed_qty

    def record_cancel(self) -> None:
        self.cancel_costs += self.cancel_cost_per_order

    def record_missed_fill(self) -> None:
        self.missed_fills += 1

    def snapshot(self, *, realized_pnl: float, unrealized_pnl: float) -> PnLAttributionSnapshot:
        return PnLAttributionSnapshot(
            symbol=self.symbol,
            strategy_name=self.strategy_name,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            spread_capture=self.spread_capture,
            fees=self.fees,
            rebates=self.rebates,
            slippage=self.slippage,
            adverse_selection=self.adverse_selection,
            inventory_pnl=unrealized_pnl,
            missed_fills=self.missed_fills,
            cancel_costs=self.cancel_costs,
        )
