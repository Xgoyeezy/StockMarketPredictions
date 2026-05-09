from __future__ import annotations

from typing import Any

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.schemas import CloseTradeRequest, OpenTradeRequest, ReplaceOrderRequest
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter, PaperLedgerPersistenceError
from backend.services.execution.mappers import BrokerExecutionError
from backend.services.execution.tradier_client import TradierApiError, TradierClient, build_tradier_paper_client
from backend.services.execution.types import ClosePositionResult


def _parse_underlying_from_occ(contract_symbol: str) -> str:
    normalized = str(contract_symbol or "").replace(" ", "").strip().upper()
    for index, character in enumerate(normalized):
        if character.isdigit():
            return normalized[:index]
    return normalized


def build_tradier_option_order_payload(
    request: OpenTradeRequest | ReplaceOrderRequest,
    *,
    contract_symbol: str,
    quantity: float,
    side: str = "buy",
    position_effect: str = "open",
) -> dict[str, Any]:
    if getattr(request, "instrument_type", "listed_option") != "listed_option":
        raise ValidationServiceError("Tradier paper execution is enabled for listed options only.")
    normalized_strategy = str(getattr(request, "option_strategy", None) or "long_option").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_strategy in {"", "single_leg", "long_debit", "long_single_leg", "buy_to_open"}:
        normalized_strategy = "long_option"
    if normalized_strategy != "long_option":
        raise ValidationServiceError("Tradier options routing currently supports long-option single-leg tickets only.")

    normalized_contract_symbol = str(contract_symbol or "").replace(" ", "").strip().upper()
    if not normalized_contract_symbol:
        raise ValidationServiceError("A valid option contract symbol is required for Tradier options routing.")

    try:
        normalized_quantity = float(quantity)
    except (TypeError, ValueError):
        normalized_quantity = 0.0
    if normalized_quantity < 1:
        raise ValidationServiceError("Options trading requires at least one full contract.")
    rounded_quantity = int(round(normalized_quantity))
    if abs(normalized_quantity - rounded_quantity) > 1e-9:
        raise ValidationServiceError("Options trading does not support fractional contracts.")

    normalized_order_type = str(request.order_type or "").strip().lower()
    if normalized_order_type not in {"market", "limit"}:
        raise ValidationServiceError("Tradier options routing currently supports market and limit orders only.")
    if str(request.time_in_force or "").strip().lower() != "day":
        raise ValidationServiceError("Tradier options routing currently supports DAY option orders only.")
    if bool(getattr(request, "extended_hours", False)):
        raise ValidationServiceError("Tradier listed-option automation is locked to regular-hours DAY orders.")

    normalized_side = str(side or "buy").strip().lower()
    normalized_effect = str(position_effect or "open").strip().lower()
    if normalized_side == "buy" and normalized_effect == "open":
        tradier_side = "buy_to_open"
    elif normalized_side == "sell" and normalized_effect == "close":
        tradier_side = "sell_to_close"
    else:
        raise ValidationServiceError("Tradier options routing supports buy-to-open and sell-to-close only.")

    payload: dict[str, Any] = {
        "class": "option",
        "symbol": str(getattr(request, "ticker", None) or _parse_underlying_from_occ(normalized_contract_symbol)).strip().upper(),
        "option_symbol": normalized_contract_symbol,
        "side": tradier_side,
        "quantity": str(rounded_quantity),
        "type": normalized_order_type,
        "duration": "day",
    }
    if normalized_order_type == "limit":
        try:
            limit_price = float(getattr(request, "limit_price", None))
        except (TypeError, ValueError):
            limit_price = 0.0
        if limit_price <= 0:
            raise ValidationServiceError("A positive limit price is required for Tradier option limit orders.")
        payload["price"] = f"{limit_price:.4f}".rstrip("0").rstrip(".")
    return payload


class TradierPaperExecutionAdapter(AlpacaPaperExecutionAdapter):
    def __init__(self, client: TradierClient | None = None) -> None:
        self.client = client or build_tradier_paper_client()

    @property
    def adapter_name(self) -> str:
        return "tradier_paper"

    def _ensure_credentials(self) -> None:
        if not settings.tradier_paper_token or not settings.tradier_paper_account_id:
            raise ValidationServiceError("Tradier paper execution requires TRADIER_PAPER_TOKEN and TRADIER_PAPER_ACCOUNT_ID.")

    def _build_submit_payload(
        self,
        *,
        request: OpenTradeRequest | ReplaceOrderRequest,
        report: dict[str, Any] | None = None,
        position: dict[str, Any] | None = None,
        order_ticket: dict[str, Any] | None = None,
        row: dict[str, Any] | None = None,
        client_order_id: str | None = None,
        side: str = "buy",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        instrument_type = str(
            getattr(request, "instrument_type", None)
            or (order_ticket or {}).get("instrument_type")
            or (row or {}).get("instrument_type")
            or "listed_option"
        ).strip().lower()
        if instrument_type != "listed_option":
            raise ValidationServiceError("Tradier is configured as the options broker only; equity orders must route to Alpaca.")
        quantity = float((position or {}).get("suggested_contracts") or (row or {}).get("suggested_contracts") or 0.0)
        contract_symbol = self._resolve_contract_symbol(
            request=request,
            report=report,
            order_ticket=order_ticket,
            row=row,
        )
        payload = build_tradier_option_order_payload(
            request,
            contract_symbol=contract_symbol,
            quantity=quantity,
            side=side,
            position_effect="close" if side == "sell" else "open",
        )
        if client_order_id:
            payload["tag"] = client_order_id
        return payload, {"class": "option", "fractionable": False}

    def close_position(
        self,
        *,
        request: CloseTradeRequest,
        target_trade: dict[str, Any],
    ) -> ClosePositionResult:
        self._ensure_credentials()
        instrument_type = str(target_trade.get("instrument_type") or "listed_option").strip().lower()
        if instrument_type != "listed_option":
            raise ValidationServiceError("Tradier paper execution is enabled for listed options only.")

        close_fraction = float(getattr(request, "close_fraction", 1.0) or 1.0)
        position_quantity = float(target_trade.get("suggested_contracts", 0) or 0.0)
        close_quantity = self._resolve_close_quantity(position_quantity, close_fraction)
        if close_quantity <= 0:
            raise ValidationServiceError("A positive quantity is required to close the Tradier paper option position.")
        contract_symbol = str(target_trade.get("contract_symbol") or "").strip().upper()
        if not contract_symbol:
            raise ValidationServiceError("Working option position is missing its contract symbol.")

        close_limit_price = float(getattr(request, "close_limit_price", None) or request.close_contract_mid)
        close_request = ReplaceOrderRequest(
            instrument_type="listed_option",
            contract_symbol=contract_symbol,
            order_type="limit",
            time_in_force="day",
            limit_price=close_limit_price,
        )
        payload = build_tradier_option_order_payload(
            close_request,
            contract_symbol=contract_symbol,
            quantity=close_quantity,
            side="sell",
            position_effect="close",
        )
        payload["symbol"] = str(target_trade.get("ticker") or _parse_underlying_from_occ(contract_symbol)).strip().upper()
        try:
            broker_response = self.client.submit_order(payload)
        except TradierApiError as exc:
            raise BrokerExecutionError(
                str(exc),
                status_code=400 if exc.status_code and exc.status_code < 500 else 502,
                details={"broker": self.adapter_name, "payload": exc.payload, "status_code": exc.status_code},
            ) from exc

        fill_price = self._coerce_number(broker_response.get("filled_avg_price"))
        close_contract_mid = float(fill_price) if fill_price is not None and fill_price > 0 else float(request.close_contract_mid)
        closed_trade = sdm.close_trade_by_index(
            trade_index=request.trade_index,
            close_underlying_price=float(request.close_underlying_price),
            close_contract_mid=close_contract_mid,
            close_fraction=close_quantity / position_quantity if position_quantity > 0 else 1.0,
            close_updates={
                "broker_name": self.adapter_name,
                "broker_close_order_id": broker_response.get("id"),
                "broker_close_status": str(broker_response.get("status") or "").strip().lower() or None,
            },
        )
        if closed_trade is None:
            raise PaperLedgerPersistenceError(
                "Tradier paper close completed, but the local closed-trades ledger row was not written.",
                details={
                    "collection_blocker": "ledger_persistence_failed",
                    "broker": self.adapter_name,
                    "stage": "close_position",
                    "expected_books": ["closed"],
                },
            )
        return ClosePositionResult(
            closed_trade=dict(closed_trade),
            broker_name=self.adapter_name,
            broker_order_id=str(broker_response.get("id") or "").strip() or None,
            broker_status=str(broker_response.get("status") or "").strip().lower() or None,
            broker_response=broker_response,
        )
