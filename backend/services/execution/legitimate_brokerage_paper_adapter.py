from __future__ import annotations

import json
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.schemas import CloseTradeRequest, OpenTradeRequest, ReplaceOrderRequest
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.base import ExecutionAdapter
from backend.services.execution.desk_adapter import DeskExecutionAdapter
from backend.services.execution.mappers import BrokerExecutionError
from backend.services.execution.types import (
    CancelOrderResult,
    ClosePositionResult,
    FillOrderResult,
    ReplaceOrderResult,
    SyncOrderResult,
    SubmitOrderResult,
)


class LegitimateBrokeragePaperExecutionAdapter(ExecutionAdapter):
    """Routes paper orders to the standalone Legitimate Brokerage /v1 API."""

    def __init__(self, fallback: ExecutionAdapter | None = None) -> None:
        self.fallback = fallback or DeskExecutionAdapter()

    @property
    def adapter_name(self) -> str:
        return "legitimate_brokerage_paper"

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
        return self.submit_order(
            request=request,
            report=report,
            live_price=live_price,
            position=position,
            trade_id=trade_id,
            order_id=order_id,
            order_ticket=order_ticket,
        )

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
        return self.submit_order(
            request=request,
            report=report,
            live_price=live_price,
            position=position,
            trade_id=trade_id,
            order_id=order_id,
            order_ticket=order_ticket,
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _coerce_quantity(request: OpenTradeRequest, position: dict[str, Any]) -> float:
        if request.requested_quantity is not None:
            return float(request.requested_quantity)
        quantity = float(position.get("suggested_contracts") or 0.0)
        if quantity <= 0:
            raise ValidationServiceError("Legitimate Brokerage paper routing requires a positive order quantity.")
        return quantity

    @staticmethod
    def _order_type(value: Any) -> str:
        normalized = str(value or "market").strip().lower()
        mapping = {
            "market": "market",
            "limit": "limit",
            "stop_market": "stop",
            "stop_limit": "stop_limit",
        }
        if normalized not in mapping:
            raise ValidationServiceError(
                f"Legitimate Brokerage paper routing does not support {normalized.replace('_', ' ')} orders yet."
            )
        return mapping[normalized]

    @staticmethod
    def _time_in_force(value: Any) -> str:
        normalized = str(value or "day").strip().lower()
        if normalized == "gtc_90d":
            return "gtc"
        if normalized in {"day", "day_ext"}:
            return normalized
        return "day"

    @classmethod
    def _side(cls, request: OpenTradeRequest) -> str:
        broker_side = str(getattr(request, "broker_side", "buy") or "buy").strip().lower()
        instrument_type = str(getattr(request, "instrument_type", "equity") or "equity").strip().lower()
        if instrument_type == "equity":
            return "sell" if broker_side == "sell" else "buy"
        return "sell_to_close" if broker_side == "sell" else "buy_to_open"

    @classmethod
    def _symbol(cls, request: OpenTradeRequest) -> str:
        instrument_type = str(getattr(request, "instrument_type", "equity") or "equity").strip().lower()
        if instrument_type == "listed_option":
            symbol = cls._clean(getattr(request, "contract_symbol", None))
            if not symbol:
                raise ValidationServiceError("Legitimate Brokerage option paper routing requires a contract symbol.")
            return symbol.upper()
        return cls._clean(getattr(request, "ticker", "")).upper()

    @classmethod
    def build_order_payload(
        cls,
        *,
        request: OpenTradeRequest,
        position: dict[str, Any],
        order_id: str,
    ) -> dict[str, Any]:
        account_id = cls._clean(settings.legitimate_brokerage_account_id)
        if not account_id:
            raise ValidationServiceError("LEGITIMATE_BROKERAGE_ACCOUNT_ID is not configured.")
        instrument_type = str(getattr(request, "instrument_type", "equity") or "equity").strip().lower()
        order_type = cls._order_type(getattr(request, "order_type", "market"))
        extended_hours = bool(getattr(request, "extended_hours", False)) or cls._time_in_force(
            getattr(request, "time_in_force", "day")
        ) == "day_ext"
        session = "after_hours" if extended_hours else "regular"
        payload: dict[str, Any] = {
            "account_id": account_id,
            "client_order_id": order_id,
            "symbol": cls._symbol(request),
            "asset_class": "option" if instrument_type == "listed_option" else "equity",
            "side": cls._side(request),
            "order_type": order_type,
            "quantity": cls._coerce_quantity(request, position),
            "time_in_force": cls._time_in_force(getattr(request, "time_in_force", "day")),
            "session": session,
            "extended_hours": extended_hours,
            "execution_mode": "paper",
            "execution_route": "internal_paper",
        }
        if order_type in {"limit", "stop_limit"}:
            if not getattr(request, "limit_price", None):
                raise ValidationServiceError("Legitimate Brokerage limit paper routing requires a positive limit price.")
            payload["limit_price"] = float(getattr(request, "limit_price", None))
        if order_type in {"stop", "stop_limit"}:
            if not getattr(request, "stop_price", None):
                raise ValidationServiceError("Legitimate Brokerage stop paper routing requires a positive stop price.")
            payload["stop_price"] = float(getattr(request, "stop_price", None))
        return payload

    def _configured(self) -> bool:
        return bool(settings.legitimate_brokerage_api_url and settings.legitimate_brokerage_api_key and settings.legitimate_brokerage_account_id)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._configured():
            raise ValidationServiceError(
                "Legitimate Brokerage paper adapter is not configured; set API URL, key, and account id."
            )
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urlrequest.Request(
            f"{settings.legitimate_brokerage_api_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-API-Key": settings.legitimate_brokerage_api_key,
            },
        )
        try:
            with urlrequest.urlopen(request, timeout=settings.legitimate_brokerage_timeout_seconds) as response:
                raw = response.read().decode("utf-8").strip()
                return json.loads(raw or "{}")
        except urlerror.HTTPError as exc:
            raw = exc.read().decode("utf-8").strip()
            try:
                details = json.loads(raw or "{}")
            except json.JSONDecodeError:
                details = {"detail": raw}
            raise BrokerExecutionError(
                f"Legitimate Brokerage API rejected the request: {details.get('detail') or exc.reason}",
                status_code=400 if exc.code < 500 else 502,
                details={"broker": self.adapter_name, "status_code": exc.code, "payload": details},
            ) from exc
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"Legitimate Brokerage paper service is unavailable: {exc}") from exc

    @staticmethod
    def _enrich_record(record: dict[str, Any], broker_order: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(record)
        enriched["broker_name"] = "legitimate_brokerage_paper"
        enriched["broker_order_id"] = broker_order.get("id")
        enriched["broker_status"] = str(broker_order.get("status") or "").strip().lower() or None
        enriched["broker_client_order_id"] = broker_order.get("client_order_id")
        enriched["broker_qty"] = broker_order.get("quantity")
        enriched["broker_filled_qty"] = broker_order.get("filled_quantity") or broker_order.get("quantity")
        enriched["broker_filled_avg_price"] = broker_order.get("average_fill_price") or broker_order.get("limit_price")
        enriched["broker_response"] = broker_order
        return enriched

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
        try:
            payload = self.build_order_payload(request=request, position=position, order_id=order_id)
            broker_order = self._request("POST", "/v1/orders", payload)
        except ConnectionError:
            return self.fallback.submit_order(
                request=request,
                report=report,
                live_price=live_price,
                position=position,
                trade_id=trade_id,
                order_id=order_id,
                order_ticket={**order_ticket, "broker_fallback": self.adapter_name},
            )

        status = str(broker_order.get("status") or "").strip().lower()
        if status == "rejected":
            raise BrokerExecutionError(
                broker_order.get("reject_reason") or "Legitimate Brokerage rejected the paper order.",
                status_code=400,
                details={"broker": self.adapter_name, "order": broker_order},
            )

        filled = status == "filled"
        if filled:
            record = sdm.open_trade_record(report, float(live_price), position, order_ticket, trade_id=trade_id, order_id=order_id)
            record = self._enrich_record(record, broker_order)
            sdm.append_open_trade(record)
            return SubmitOrderResult(
                position_opened=True,
                record=record,
                pending_order=None,
                broker_name=self.adapter_name,
                broker_order_id=str(broker_order.get("id") or ""),
                broker_status=status,
                broker_response=broker_order,
            )

        pending_order = sdm.pending_order_record(report, float(live_price), position, order_ticket, trade_id=trade_id, order_id=order_id)
        pending_order = self._enrich_record(pending_order, broker_order)
        sdm.append_pending_order(pending_order)
        return SubmitOrderResult(
            position_opened=False,
            record=pending_order,
            pending_order=pending_order,
            broker_name=self.adapter_name,
            broker_order_id=str(broker_order.get("id") or ""),
            broker_status=status or "accepted",
            broker_response=broker_order,
        )

    def close_position(self, *, request: CloseTradeRequest, target_trade: dict[str, Any]) -> ClosePositionResult:
        return self.fallback.close_position(request=request, target_trade=target_trade)

    def cancel_order(self, *, order_id: str) -> CancelOrderResult | None:
        return self.fallback.cancel_order(order_id=order_id)

    def replace_order(
        self,
        *,
        order_id: str,
        request: ReplaceOrderRequest,
        order_ticket: dict[str, Any],
    ) -> ReplaceOrderResult | None:
        return self.fallback.replace_order(order_id=order_id, request=request, order_ticket=order_ticket)

    def fill_order(self, *, order_id: str, live_price: float) -> FillOrderResult | None:
        return self.fallback.fill_order(order_id=order_id, live_price=live_price)

    def sync_order(self, *, pending_order: dict[str, Any]) -> SyncOrderResult | None:
        broker_order_id = self._clean((pending_order or {}).get("broker_order_id"))
        if not broker_order_id:
            return self.fallback.sync_order(pending_order=pending_order)
        try:
            broker_order = self._request("GET", f"/v1/orders/{broker_order_id}")
        except ConnectionError:
            return SyncOrderResult(
                state="skipped",
                pending_order=dict(pending_order),
                broker_name=self.adapter_name,
                broker_order_id=broker_order_id,
                detail="Legitimate Brokerage paper service is unavailable; local pending order was left unchanged.",
            )
        status = str(broker_order.get("status") or "").strip().lower()
        return SyncOrderResult(
            state="filled" if status == "filled" else status or "working",
            pending_order=dict(pending_order),
            broker_name=self.adapter_name,
            broker_order_id=broker_order_id,
            broker_status=status or None,
            broker_response=broker_order,
            detail="Legitimate Brokerage paper order status refreshed.",
        )
