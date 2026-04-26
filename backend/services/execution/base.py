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

    @abstractmethod
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
