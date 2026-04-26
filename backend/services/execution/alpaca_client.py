from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from backend.core.config import settings


class AlpacaApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(message)


def _perform_request(
    *,
    base_url: str,
    method: str,
    path: str,
    timeout_seconds: int,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{base_url.rstrip('/')}{normalized_path}"
    if query:
        compact_query = {key: value for key, value in query.items() if value is not None}
        if compact_query:
            url = f"{url}?{parse.urlencode(compact_query)}"

    body = None
    request_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    req = request.Request(url, data=body, headers=request_headers, method=method.upper())
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
        message = str(payload_data.get("message") or payload_data.get("error") or raw or f"Alpaca API error ({exc.code}).")
        raise AlpacaApiError(message, status_code=exc.code, payload=payload_data) from exc
    except error.URLError as exc:
        raise AlpacaApiError(f"Could not reach Alpaca Trading API: {exc.reason}") from exc


@dataclass(frozen=True)
class AlpacaTradingClient:
    api_key_id: str
    api_secret_key: str
    base_url: str
    timeout_seconds: int = 10

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
            method=method,
            path=path,
            timeout_seconds=self.timeout_seconds,
            headers={
                "Accept": "application/json",
                "Apca-Api-Key-Id": self.api_key_id,
                "Apca-Api-Secret-Key": self.api_secret_key,
            },
            payload=payload,
            query=query,
        )

    def get_asset(self, symbol: str) -> dict[str, Any] | None:
        return self._request("GET", f"/v2/assets/{str(symbol or '').strip().upper()}")

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/v2/account") or {}

    def list_positions(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/v2/positions")
        if response is None:
            return []
        if isinstance(response.get("data"), list):
            return [item for item in response["data"] if isinstance(item, dict)]
        return []

    def list_orders(
        self,
        *,
        status: str = "all",
        limit: int = 100,
        after: str | None = None,
        until: str | None = None,
        nested: bool = False,
    ) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/v2/orders",
            query={
                "status": status,
                "limit": limit,
                "after": after,
                "until": until,
                "nested": str(bool(nested)).lower(),
            },
        )
        if response is None:
            return []
        if isinstance(response.get("data"), list):
            return [item for item in response["data"] if isinstance(item, dict)]
        return []

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v2/orders", payload=payload) or {}

    def replace_order(self, broker_order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/v2/orders/{broker_order_id}", payload=payload) or {}

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v2/orders/{broker_order_id}") or {}

    def get_order(self, broker_order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/orders/{broker_order_id}") or {}

    def close_position(self, symbol: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v2/positions/{str(symbol or '').strip().upper()}") or {}


@dataclass(frozen=True)
class AlpacaOAuthTradingClient:
    access_token: str
    base_url: str
    timeout_seconds: int = 10

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
            method=method,
            path=path,
            timeout_seconds=self.timeout_seconds,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
            payload=payload,
            query=query,
        )

    def get_asset(self, symbol: str) -> dict[str, Any] | None:
        return self._request("GET", f"/v2/assets/{str(symbol or '').strip().upper()}")

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/v2/account") or {}

    def list_positions(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/v2/positions")
        if response is None:
            return []
        if isinstance(response.get("data"), list):
            return [item for item in response["data"] if isinstance(item, dict)]
        return []

    def list_orders(
        self,
        *,
        status: str = "all",
        limit: int = 100,
        after: str | None = None,
        until: str | None = None,
        nested: bool = False,
    ) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/v2/orders",
            query={
                "status": status,
                "limit": limit,
                "after": after,
                "until": until,
                "nested": str(bool(nested)).lower(),
            },
        )
        if response is None:
            return []
        if isinstance(response.get("data"), list):
            return [item for item in response["data"] if isinstance(item, dict)]
        return []

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v2/orders", payload=payload) or {}

    def replace_order(self, broker_order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/v2/orders/{broker_order_id}", payload=payload) or {}

    def cancel_order(self, broker_order_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v2/orders/{broker_order_id}") or {}

    def get_order(self, broker_order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/orders/{broker_order_id}") or {}

    def close_position(self, symbol: str) -> dict[str, Any]:
        return self._request("DELETE", f"/v2/positions/{str(symbol or '').strip().upper()}") or {}


def build_alpaca_paper_client() -> AlpacaTradingClient:
    return AlpacaTradingClient(
        api_key_id=settings.alpaca_api_key_id,
        api_secret_key=settings.alpaca_api_secret_key,
        base_url=settings.alpaca_paper_trading_api_url,
        timeout_seconds=settings.alpaca_trading_request_timeout_seconds,
    )


def build_alpaca_live_client() -> AlpacaTradingClient:
    return AlpacaTradingClient(
        api_key_id=settings.alpaca_live_api_key_id or settings.alpaca_api_key_id,
        api_secret_key=settings.alpaca_live_api_secret_key or settings.alpaca_api_secret_key,
        base_url=settings.alpaca_live_trading_api_url,
        timeout_seconds=settings.alpaca_trading_request_timeout_seconds,
    )


def build_alpaca_oauth_client(*, access_token: str, account_environment: str) -> AlpacaOAuthTradingClient:
    normalized_environment = str(account_environment or "paper").strip().lower() or "paper"
    base_url = settings.alpaca_live_trading_api_url if normalized_environment == "live" else settings.alpaca_paper_trading_api_url
    return AlpacaOAuthTradingClient(
        access_token=access_token,
        base_url=base_url,
        timeout_seconds=settings.alpaca_trading_request_timeout_seconds,
    )
