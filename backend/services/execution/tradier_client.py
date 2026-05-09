from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request

from backend.core.config import settings
from backend.services.execution.alpaca_client import AlpacaApiError


class TradierApiError(AlpacaApiError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(message)


def _compact_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in dict(payload or {}).items() if value is not None}


def _perform_request(
    *,
    base_url: str,
    token: str,
    method: str,
    path: str,
    timeout_seconds: int,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{base_url.rstrip('/')}{normalized_path}"
    compact_query = _compact_payload(query)
    if compact_query:
        url = f"{url}?{parse.urlencode(compact_query)}"

    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        body = parse.urlencode(_compact_payload(payload)).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8").strip()
            if not raw:
                return None
            parsed_body = json.loads(raw)
            return parsed_body if isinstance(parsed_body, dict) else {"data": parsed_body}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8").strip()
        payload_data: dict[str, Any] = {}
        if raw:
            try:
                parsed_error = json.loads(raw)
                if isinstance(parsed_error, dict):
                    payload_data = parsed_error
            except Exception:
                payload_data = {"raw": raw}
        message = str(payload_data.get("message") or payload_data.get("error") or raw or f"Tradier API error ({exc.code}).")
        raise TradierApiError(message, status_code=exc.code, payload=payload_data) from exc
    except error.URLError as exc:
        raise TradierApiError(f"Could not reach Tradier API: {exc.reason}") from exc


def _unwrap_order(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(payload or {})
    order = payload.get("order")
    if isinstance(order, dict):
        return order
    orders = payload.get("orders")
    if isinstance(orders, dict):
        nested = orders.get("order")
        if isinstance(nested, dict):
            return nested
        if isinstance(nested, list) and nested and isinstance(nested[0], dict):
            return nested[0]
    return payload


def _unwrap_quote_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    quotes = dict(payload or {}).get("quotes")
    if isinstance(quotes, dict):
        quote = quotes.get("quote")
        if isinstance(quote, list):
            return [item for item in quote if isinstance(item, dict)]
        if isinstance(quote, dict):
            return [quote]
    return []


def _unwrap_option_items(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    options = dict(payload or {}).get("options")
    if isinstance(options, dict):
        option = options.get("option")
        if isinstance(option, list):
            return [item for item in option if isinstance(item, dict)]
        if isinstance(option, dict):
            return [option]
    return []


def _unwrap_expiration_items(payload: dict[str, Any] | None) -> list[str]:
    expirations = dict(payload or {}).get("expirations")
    if isinstance(expirations, dict):
        dates = expirations.get("date")
        if isinstance(dates, list):
            return [str(item).strip() for item in dates if str(item or "").strip()]
        if str(dates or "").strip():
            return [str(dates).strip()]
    return []


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _coerce_int(value: Any) -> int:
    numeric = _coerce_float(value)
    return int(numeric or 0)


def _coerce_epoch_timestamp(value: Any) -> str | None:
    numeric = _coerce_float(value)
    if numeric is None or numeric <= 0:
        return None
    if numeric > 100000000000:
        numeric = numeric / 1000.0
    return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()


def _coerce_tradier_timestamp(value: Any) -> str | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return _coerce_epoch_timestamp(value)
    text = str(value or "").strip()
    if text.isdigit():
        return _coerce_epoch_timestamp(text)
    if "T" not in text and len(text) == 19:
        text = f"{text.replace(' ', 'T')}+00:00"
    return text


def normalize_tradier_order(order_payload: dict[str, Any] | None, *, fallback_id: str | None = None) -> dict[str, Any]:
    raw_order = _unwrap_order(order_payload)
    order_id = str(raw_order.get("id") or fallback_id or "").strip()
    status = str(raw_order.get("status") or "").strip().lower() or "accepted"
    asset_class = str(raw_order.get("class") or raw_order.get("asset_class") or "").strip().lower()
    normalized = {
        "id": order_id,
        "status": status,
        "qty": raw_order.get("quantity") or raw_order.get("qty"),
        "filled_qty": raw_order.get("exec_quantity") or raw_order.get("filled_qty"),
        "filled_avg_price": raw_order.get("avg_fill_price") or raw_order.get("filled_avg_price"),
        "submitted_at": raw_order.get("create_date") or raw_order.get("submitted_at"),
        "updated_at": raw_order.get("transaction_date") or raw_order.get("updated_at"),
        "asset_class": "option" if asset_class == "option" else asset_class or "option",
        "client_order_id": raw_order.get("tag") or raw_order.get("client_order_id"),
        "symbol": raw_order.get("symbol"),
        "option_symbol": raw_order.get("option_symbol"),
        "raw": raw_order,
    }
    return normalized


def normalize_tradier_balances(payload: dict[str, Any] | None) -> dict[str, Any]:
    balances = dict(payload or {}).get("balances")
    if isinstance(balances, dict):
        source = balances
    else:
        source = dict(payload or {})
    margin = source.get("margin") if isinstance(source.get("margin"), dict) else {}
    cash = source.get("cash") if isinstance(source.get("cash"), dict) else {}
    pdt = source.get("pdt") if isinstance(source.get("pdt"), dict) else {}
    option_buying_power = (
        _coerce_float(margin.get("option_buying_power"))
        or _coerce_float(pdt.get("option_buying_power"))
        or _coerce_float(source.get("option_buying_power"))
    )
    buying_power = (
        _coerce_float(source.get("total_cash"))
        or _coerce_float(cash.get("cash_available"))
        or _coerce_float(margin.get("stock_buying_power"))
        or _coerce_float(pdt.get("stock_buying_power"))
        or option_buying_power
    )
    equity = _coerce_float(source.get("total_equity")) or _coerce_float(source.get("equity")) or _coerce_float(source.get("total_cash"))
    cash_value = _coerce_float(source.get("total_cash")) or _coerce_float(cash.get("cash_available"))
    return {
        "equity": equity,
        "cash": cash_value,
        "buying_power": buying_power,
        "option_buying_power": option_buying_power,
        "account_type": source.get("type"),
        "pending_cash": _coerce_float(source.get("pending_cash")),
        "option_requirement": _coerce_float(source.get("option_requirement")),
    }


def normalize_tradier_option_contract(row: dict[str, Any], *, quote_timestamp: Any = None) -> dict[str, Any]:
    row = dict(row or {})
    greeks = row.get("greeks") if isinstance(row.get("greeks"), dict) else {}
    timestamp = (
        _coerce_tradier_timestamp(row.get("bid_date"))
        or _coerce_tradier_timestamp(row.get("ask_date"))
        or _coerce_tradier_timestamp(row.get("trade_date"))
        or _coerce_tradier_timestamp(quote_timestamp)
    )
    return {
        "symbol": str(row.get("symbol") or row.get("option_symbol") or "").replace(" ", "").upper(),
        "details": {
            "contract_type": row.get("option_type") or row.get("type"),
            "expiration_date": row.get("expiration_date"),
            "strike_price": row.get("strike"),
        },
        "latest_quote": {
            "bid": row.get("bid"),
            "ask": row.get("ask"),
            "timestamp": timestamp,
        },
        "latest_trade": {"price": row.get("last")},
        "day": {"volume": row.get("volume")},
        "open_interest": row.get("open_interest"),
        "implied_volatility": greeks.get("mid_iv") or greeks.get("smv_vol"),
        "source": "tradier_options_chain",
        "raw": row,
    }


@dataclass(frozen=True)
class TradierClient:
    token: str
    account_id: str
    base_url: str
    timeout_seconds: int = 10

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.account_id)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return _perform_request(
            base_url=self.base_url,
            token=self.token,
            method=method,
            path=path,
            timeout_seconds=self.timeout_seconds,
            payload=payload,
            query=query,
        )

    def get_account_balances(self, account_id: str | None = None) -> dict[str, Any]:
        account = str(account_id or self.account_id or "").strip()
        return self._request("GET", f"/accounts/{account}/balances") or {}

    def get_quotes(self, symbols: list[str] | tuple[str, ...] | str) -> list[dict[str, Any]]:
        if isinstance(symbols, str):
            symbol_param = symbols
        else:
            symbol_param = ",".join(str(item).strip().upper() for item in symbols if str(item or "").strip())
        return _unwrap_quote_items(self._request("GET", "/markets/quotes", query={"symbols": symbol_param, "greeks": "true"}))

    def get_option_expirations(self, symbol: str) -> list[str]:
        return _unwrap_expiration_items(
            self._request(
                "GET",
                "/markets/options/expirations",
                query={"symbol": str(symbol or "").strip().upper(), "includeAllRoots": "true"},
            )
        )

    def get_option_chain(self, symbol: str, expiration: str, *, greeks: bool = True) -> list[dict[str, Any]]:
        return _unwrap_option_items(
            self._request(
                "GET",
                "/markets/options/chains",
                query={
                    "symbol": str(symbol or "").strip().upper(),
                    "expiration": str(expiration or "").strip(),
                    "greeks": str(bool(greeks)).lower(),
                },
            )
        )

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", f"/accounts/{self.account_id}/orders", payload=payload) or {}
        return normalize_tradier_order(response)

    def get_order(self, broker_order_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/accounts/{self.account_id}/orders/{broker_order_id}") or {}
        return normalize_tradier_order(response, fallback_id=str(broker_order_id or ""))

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/accounts/{self.account_id}/orders/{broker_order_id}") or {}
        normalized = normalize_tradier_order(response, fallback_id=str(broker_order_id or ""))
        normalized["status"] = normalized.get("status") or "canceled"
        return normalized

    def replace_order(self, broker_order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.cancel_order(broker_order_id)
        return self.submit_order(payload)

    def list_positions(self, account_id: str | None = None) -> list[dict[str, Any]]:
        account = str(account_id or self.account_id or "").strip()
        response = self._request("GET", f"/accounts/{account}/positions") or {}
        positions = response.get("positions")
        if isinstance(positions, dict):
            position = positions.get("position")
            if isinstance(position, list):
                return [item for item in position if isinstance(item, dict)]
            if isinstance(position, dict):
                return [position]
        return []


def build_tradier_paper_client() -> TradierClient:
    return TradierClient(
        token=settings.tradier_paper_token,
        account_id=settings.tradier_paper_account_id,
        base_url=settings.tradier_sandbox_api_url,
        timeout_seconds=settings.tradier_request_timeout_seconds,
    )


def build_tradier_live_market_data_client() -> TradierClient:
    return TradierClient(
        token=settings.tradier_live_token,
        account_id=settings.tradier_live_account_id,
        base_url=settings.tradier_api_url,
        timeout_seconds=settings.tradier_request_timeout_seconds,
    )
