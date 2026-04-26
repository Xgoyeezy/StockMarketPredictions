from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubmitOrderResult:
    position_opened: bool
    record: dict[str, Any]
    pending_order: dict[str, Any] | None
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    broker_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class ClosePositionResult:
    closed_trade: dict[str, Any]
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    broker_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class CancelOrderResult:
    canceled_order: dict[str, Any]
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    broker_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReplaceOrderResult:
    updated_order: dict[str, Any]
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    broker_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class FillOrderResult:
    filled_record: dict[str, Any]
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    broker_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class SyncOrderResult:
    state: str
    pending_order: dict[str, Any] | None = None
    opened_record: dict[str, Any] | None = None
    terminal_order: dict[str, Any] | None = None
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_status: str | None = None
    broker_response: dict[str, Any] | None = None
    detail: str | None = None
    slippage_dollars: float | None = None
    slippage_bps: float | None = None
