from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.schemas import CloseTradeRequest, OpenTradeRequest, ReplaceOrderRequest
from backend.services.execution.types import (
    CancelOrderResult,
    ClosePositionResult,
    FillOrderResult,
    ReplaceOrderResult,
    SyncOrderResult,
    SubmitOrderResult,
)


class ExecutionAdapter(ABC):
    @property
    @abstractmethod
    def adapter_name(self) -> str:
        raise NotImplementedError

    # ---- Provider-agnostic v2 contract (preferred) ----
    # Brokers are "dumb pipes": translate internal intent -> broker payload and normalize responses.

    @abstractmethod
    def submit_equity_order(
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
        raise NotImplementedError

    @abstractmethod
    def submit_option_order(
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
        raise NotImplementedError

    @abstractmethod
    def close_position(
        self,
        *,
        request: CloseTradeRequest,
        target_trade: dict[str, Any],
    ) -> ClosePositionResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, *, order_id: str) -> CancelOrderResult | None:
        raise NotImplementedError

    @abstractmethod
    def replace_order(
        self,
        *,
        order_id: str,
        request: ReplaceOrderRequest,
        order_ticket: dict[str, Any],
    ) -> ReplaceOrderResult | None:
        raise NotImplementedError

    @abstractmethod
    def fill_order(self, *, order_id: str, live_price: float) -> FillOrderResult | None:
        raise NotImplementedError

    @abstractmethod
    def sync_order(self, *, pending_order: dict[str, Any]) -> SyncOrderResult | None:
        raise NotImplementedError

    def sync_open_orders(self) -> list[SyncOrderResult]:
        return []

    def sync_positions(self) -> dict[str, Any]:
        return {}

    # ---- Legacy compatibility ----
    # Older call sites use `submit_order`; keep as a thin alias so the router can migrate safely.
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
        instrument_type = str(getattr(request, "instrument_type", "equity") or "equity").strip().lower()
        if instrument_type == "listed_option":
            return self.submit_option_order(
                request=request,
                report=report,
                live_price=live_price,
                position=position,
                trade_id=trade_id,
                order_id=order_id,
                order_ticket=order_ticket,
            )
        return self.submit_equity_order(
            request=request,
            report=report,
            live_price=live_price,
            position=position,
            trade_id=trade_id,
            order_id=order_id,
            order_ticket=order_ticket,
        )
