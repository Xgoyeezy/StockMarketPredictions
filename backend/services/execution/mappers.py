from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from backend.schemas import OpenTradeRequest, ReplaceOrderRequest
from backend.services.exceptions import ServiceError, ValidationServiceError

_WORKING_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "queued",
    "replaced",
}
_FILLED_STATUSES = {"filled"}
_CANCELED_STATUSES = {"canceled", "cancelled"}
_EXPIRED_STATUSES = {"expired", "done_for_day"}
_REJECTED_STATUSES = {"rejected", "suspended", "stopped"}


class BrokerExecutionError(ServiceError):
    error_code = "broker_execution_error"
    default_message = "The broker rejected or failed the order."


def normalize_alpaca_status(value: Any) -> str:
    return str(value or "").strip().lower()


def is_filled_alpaca_status(value: Any) -> bool:
    return normalize_alpaca_status(value) in _FILLED_STATUSES


def is_working_alpaca_status(value: Any) -> bool:
    return normalize_alpaca_status(value) in _WORKING_STATUSES


def is_canceled_alpaca_status(value: Any) -> bool:
    return normalize_alpaca_status(value) in _CANCELED_STATUSES


def is_expired_alpaca_status(value: Any) -> bool:
    return normalize_alpaca_status(value) in _EXPIRED_STATUSES


def is_rejected_alpaca_status(value: Any) -> bool:
    return normalize_alpaca_status(value) in _REJECTED_STATUSES


def _coerce_fractional_number(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_alpaca_limit_price(value: Any, *, instrument_type: str) -> str | None:
    if value in (None, "", "nan"):
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if price <= 0:
        return None
    normalized_instrument = str(instrument_type or "equity").strip().lower()
    increment = Decimal("0.01") if normalized_instrument == "listed_option" or price >= Decimal("1") else Decimal("0.0001")
    rounded = price.quantize(increment, rounding=ROUND_HALF_UP)
    return format(rounded.normalize(), "f")


def map_time_in_force_to_alpaca(time_in_force: str) -> str:
    normalized = str(time_in_force or "").strip().lower()
    if normalized in {"day", "day_ext"}:
        return "day"
    if normalized == "gtc_90d":
        return "gtc"
    raise ValidationServiceError(f"Unsupported time in force for Alpaca paper trading: {time_in_force}.")


def build_alpaca_equity_order_payload(
    request: OpenTradeRequest | ReplaceOrderRequest,
    *,
    ticker: str,
    quantity: float,
    client_order_id: str | None = None,
    side: str = "buy",
) -> dict[str, Any]:
    if getattr(request, "instrument_type", "equity") != "equity":
        raise ValidationServiceError("Alpaca paper execution is currently enabled for equities only.")
    normalized_quantity = _coerce_fractional_number(quantity)
    if normalized_quantity is None or normalized_quantity <= 0:
        raise ValidationServiceError("Paper trading requires a positive share quantity.")

    normalized_order_type = str(request.order_type or "").strip().lower()
    if normalized_order_type not in {"market", "limit"}:
        raise ValidationServiceError("Alpaca paper execution currently supports market and limit equity orders only.")

    normalized_time_in_force = str(request.time_in_force or "").strip().lower()
    extended_hours = bool(getattr(request, "extended_hours", False) or normalized_time_in_force == "day_ext")
    if extended_hours and normalized_order_type != "limit":
        raise ValidationServiceError("Extended-hours equity routing requires a limit order.")

    payload: dict[str, Any] = {
        "symbol": str(ticker or "").strip().upper(),
        "qty": f"{normalized_quantity:.6f}".rstrip("0").rstrip("."),
        "side": str(side or "buy").strip().lower() or "buy",
        "type": normalized_order_type,
        "time_in_force": map_time_in_force_to_alpaca(request.time_in_force),
    }
    if extended_hours:
        payload["extended_hours"] = True
    if client_order_id:
        payload["client_order_id"] = client_order_id

    if normalized_order_type == "limit":
        limit_price = format_alpaca_limit_price(getattr(request, "limit_price", None), instrument_type="equity")
        if limit_price is None:
            raise ValidationServiceError("A positive limit price is required for Alpaca paper limit orders.")
        payload["limit_price"] = limit_price

    return payload


def build_alpaca_option_order_payload(
    request: OpenTradeRequest | ReplaceOrderRequest,
    *,
    contract_symbol: str,
    quantity: float,
    client_order_id: str | None = None,
    side: str = "buy",
    position_effect: str = "open",
) -> dict[str, Any]:
    if getattr(request, "instrument_type", "listed_option") != "listed_option":
        raise ValidationServiceError("Alpaca options routing requires a listed-option ticket.")

    normalized_strategy = str(
        getattr(request, "option_strategy", None) or "long_option"
    ).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_strategy in {"", "single_leg", "long_debit", "long_single_leg", "buy_to_open"}:
        normalized_strategy = "long_option"
    if normalized_strategy != "long_option":
        raise ValidationServiceError("Alpaca options routing currently supports long-option single-leg tickets only.")

    normalized_side = str(side or "buy").strip().lower() or "buy"
    normalized_position_effect = str(position_effect or "open").strip().lower() or "open"
    if normalized_side not in {"buy", "sell"}:
        raise ValidationServiceError("Alpaca options routing supports buy-to-open and sell-to-close only.")
    if normalized_position_effect not in {"open", "close"}:
        raise ValidationServiceError("Alpaca options routing supports open and close position effects only.")
    if normalized_side == "buy" and normalized_position_effect != "open":
        raise ValidationServiceError("Alpaca options routing supports buy-to-open and sell-to-close only.")
    if normalized_side == "sell" and normalized_position_effect != "close":
        raise ValidationServiceError("Alpaca options routing currently supports buy-to-open long option tickets only.")

    normalized_contract_symbol = str(contract_symbol or "").strip().upper()
    if not normalized_contract_symbol:
        raise ValidationServiceError("A valid option contract symbol is required for Alpaca options routing.")

    normalized_quantity = _coerce_fractional_number(quantity)
    if normalized_quantity is None or normalized_quantity < 1:
        raise ValidationServiceError("Options trading requires at least one full contract.")
    rounded_quantity = int(round(normalized_quantity))
    if abs(normalized_quantity - rounded_quantity) > 1e-9:
        raise ValidationServiceError("Options trading does not support fractional contracts.")

    normalized_order_type = str(request.order_type or "").strip().lower()
    if normalized_order_type not in {"market", "limit"}:
        raise ValidationServiceError("Alpaca options routing currently supports market and limit orders only.")

    normalized_time_in_force = str(request.time_in_force or "").strip().lower()
    if normalized_time_in_force != "day":
        raise ValidationServiceError("Alpaca options routing currently supports DAY orders only.")

    if bool(getattr(request, "extended_hours", False)) or normalized_time_in_force == "day_ext":
        raise ValidationServiceError("Alpaca options routing is currently locked to regular-hours execution only.")

    payload: dict[str, Any] = {
        "symbol": normalized_contract_symbol,
        "qty": str(rounded_quantity),
        "side": normalized_side,
        "type": normalized_order_type,
        "time_in_force": "day",
    }
    if client_order_id:
        payload["client_order_id"] = client_order_id

    if normalized_order_type == "limit":
        limit_price = format_alpaca_limit_price(
            getattr(request, "limit_price", None),
            instrument_type="listed_option",
        )
        if limit_price is None:
            raise ValidationServiceError("A positive limit price is required for Alpaca option limit orders.")
        payload["limit_price"] = limit_price

    return payload


def enrich_local_order_record(
    record: dict[str, Any],
    *,
    broker_name: str,
    broker_order: dict[str, Any] | None,
    asset_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(record)
    broker_order = broker_order or {}
    asset_metadata = asset_metadata or {}
    enriched["broker_name"] = broker_name
    enriched["broker_order_id"] = str(broker_order.get("id") or "").strip() or None
    enriched["broker_status"] = normalize_alpaca_status(broker_order.get("status")) or None
    enriched["broker_client_order_id"] = str(broker_order.get("client_order_id") or "").strip() or None
    enriched["broker_asset_class"] = str(
        broker_order.get("asset_class") or broker_order.get("asset_class_name") or asset_metadata.get("class") or ""
    ).strip() or "us_equity"
    enriched["broker_fractionable"] = bool(
        asset_metadata.get("fractionable")
        if asset_metadata.get("fractionable") is not None
        else broker_order.get("fractionable")
    )
    enriched["broker_qty"] = _coerce_fractional_number(broker_order.get("qty"))
    enriched["broker_filled_qty"] = _coerce_fractional_number(broker_order.get("filled_qty"))
    enriched["broker_notional"] = _coerce_fractional_number(broker_order.get("notional"))
    enriched["broker_filled_avg_price"] = _coerce_fractional_number(broker_order.get("filled_avg_price"))
    enriched["broker_submitted_at"] = broker_order.get("submitted_at")
    enriched["broker_updated_at"] = broker_order.get("updated_at")
    return enriched
