from __future__ import annotations

from typing import Any

from backend import stock_direction_model as sdm
from backend.schemas import CloseTradeRequest, OpenTradeRequest, ReplaceOrderRequest
from backend.services.execution.base import ExecutionAdapter
from backend.services.execution.types import (
    CancelOrderResult,
    ClosePositionResult,
    FillOrderResult,
    ReplaceOrderResult,
    SyncOrderResult,
    SubmitOrderResult,
)


class DeskExecutionAdapter(ExecutionAdapter):
    @staticmethod
    def _resolve_close_quantity(quantity: float, close_fraction: float) -> float:
        normalized_quantity = max(float(quantity or 0.0), 0.0)
        normalized_fraction = min(max(float(close_fraction or 1.0), 0.0), 1.0)
        if normalized_quantity <= 0 or normalized_fraction <= 0:
            return 0.0
        if normalized_fraction >= 1:
            return normalized_quantity
        if normalized_quantity <= 1:
            return min(
                normalized_quantity,
                max(0.001, float(int((normalized_quantity * normalized_fraction) * 1000)) / 1000.0),
            )
        close_quantity = max(1.0, float(int(round(normalized_quantity * normalized_fraction))))
        return min(close_quantity, normalized_quantity)

    @property
    def adapter_name(self) -> str:
        return "desk"

    def submit_order(
        self,
        *,
        request: OpenTradeRequest,
        report: dict[str, Any],
        live_price: float,
        position: dict[str, Any],
        trade_id: str,
        order_id: str,
        order_ticket: dict[str, Any],
    ) -> SubmitOrderResult:
        position_opened = request.order_type == "market"
        if position_opened:
            record = sdm.open_trade_record(
                report,
                float(live_price),
                position,
                order_ticket,
                trade_id=trade_id,
                order_id=order_id,
            )
            sdm.append_open_trade(record)
            return SubmitOrderResult(
                position_opened=True,
                record=record,
                pending_order=None,
            )

        record = sdm.pending_order_record(
            report,
            float(live_price),
            position,
            order_ticket,
            trade_id=trade_id,
            order_id=order_id,
        )
        sdm.append_pending_order(record)
        return SubmitOrderResult(
            position_opened=False,
            record=record,
            pending_order=record,
        )

    def close_position(
        self,
        *,
        request: CloseTradeRequest,
        target_trade: dict[str, Any],
    ) -> ClosePositionResult:
        contracts = float(target_trade.get("suggested_contracts", 0) or 0.0)
        close_fraction = float(getattr(request, "close_fraction", 1.0) or 1.0)
        close_quantity = self._resolve_close_quantity(contracts, close_fraction)
        sdm.close_trade_by_index(
            trade_index=request.trade_index,
            close_underlying_price=float(request.close_underlying_price),
            close_contract_mid=float(request.close_contract_mid),
            close_fraction=close_quantity / contracts if contracts > 0 else 1.0,
        )
        remaining_contracts = max(contracts - close_quantity, 0.0)
        enriched_trade = dict(target_trade)
        enriched_trade["close_fraction"] = close_quantity / contracts if contracts > 0 else 1.0
        enriched_trade["closed_contracts"] = close_quantity
        enriched_trade["remaining_contracts_after_close"] = remaining_contracts
        enriched_trade["status"] = "CLOSED" if remaining_contracts == 0 else "PARTIAL"
        return ClosePositionResult(closed_trade=enriched_trade)

    def cancel_order(self, *, order_id: str) -> CancelOrderResult | None:
        canceled = sdm.cancel_pending_order(order_id)
        if canceled is None:
            return None
        return CancelOrderResult(canceled_order=canceled)

    def replace_order(
        self,
        *,
        order_id: str,
        request: ReplaceOrderRequest,
        order_ticket: dict[str, Any],
    ) -> ReplaceOrderResult | None:
        updated = sdm.replace_pending_order(order_id, order_ticket)
        if updated is None:
            return None
        return ReplaceOrderResult(updated_order=updated)

    def fill_order(self, *, order_id: str, live_price: float) -> FillOrderResult | None:
        filled = sdm.fill_pending_order(order_id, float(live_price))
        if filled is None:
            return None
        return FillOrderResult(filled_record=filled)

    def sync_order(self, *, pending_order: dict[str, Any]) -> SyncOrderResult | None:
        if not pending_order:
            return None
        return SyncOrderResult(
            state="skipped",
            pending_order=dict(pending_order),
            broker_name=self.adapter_name,
            detail="Desk-managed orders do not require broker reconciliation.",
        )
