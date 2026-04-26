from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

from hft.execution.order_state import OrderLifecycleState
from hft.live.http import parse_timestamp_ns, request_json
from hft.market_data.sessions import is_extended_session, normalize_market_session
from hft.utils.env import merged_env


@dataclass
class PaperOrderIntent:
    order_id: str
    symbol: str
    strategy_name: str
    side: str
    price: float
    quantity: float
    decision_timestamp: int
    quote_width: float
    metadata: dict[str, Any] = field(default_factory=dict)
    risk_checked: bool = False
    risk_reason: str = "unvalidated"
    session: str = "regular"
    order_type: str = "limit"


@dataclass(frozen=True)
class PaperExecutionReport:
    order_id: str
    broker_order_id: str
    symbol: str
    strategy_name: str
    side: str
    price: float
    quantity: float
    filled_quantity: float
    average_fill_price: float
    decision_timestamp: int
    send_timestamp: int
    exchange_receive_timestamp: int
    ack_timestamp: int
    updated_at_ns: int
    fill_timestamp: int | None
    state: OrderLifecycleState
    accepted: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PaperExecutionAdapter(ABC):
    @abstractmethod
    def check_connection(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, broker_order_id: str, *, symbol: str | None = None) -> PaperExecutionReport:
        raise NotImplementedError

    @abstractmethod
    def sync_orders(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        raise NotImplementedError

    @abstractmethod
    def flatten_positions(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        raise NotImplementedError


@dataclass
class AlpacaPaperExecutionAdapter(PaperExecutionAdapter):
    api_key_id: str
    api_secret_key: str
    base_url: str = "https://paper-api.alpaca.markets"
    request_timeout_seconds: int = 10

    def __post_init__(self) -> None:
        self._known_intents: dict[str, PaperOrderIntent] = {}

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "AlpacaPaperExecutionAdapter":
        env = merged_env(env_file)
        api_key_id = str(env.get("APCA_API_KEY_ID", env.get("ALPACA_API_KEY_ID", "")) or "").strip()
        api_secret_key = str(env.get("APCA_API_SECRET_KEY", env.get("ALPACA_API_SECRET_KEY", "")) or "").strip()
        base_url = str(env.get("ALPACA_PAPER_API_BASE_URL", "https://paper-api.alpaca.markets") or "").strip()
        timeout_seconds = int(env.get("ALPACA_TRADING_REQUEST_TIMEOUT_SECONDS", "10") or 10)
        return cls(
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            base_url=base_url.rstrip("/"),
            request_timeout_seconds=timeout_seconds,
        )

    def check_connection(self) -> dict[str, Any]:
        response = request_json(
            f"{self.base_url}/v2/account",
            headers=self._headers(),
            timeout_seconds=self.request_timeout_seconds,
        )
        return {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "message": response.message or ("ok" if response.status_code == 200 else "paper account unavailable"),
            "payload": response.payload,
        }

    def submit_order(self, intent: PaperOrderIntent) -> PaperExecutionReport:
        if not intent.risk_checked or intent.risk_reason != "allowed":
            return PaperExecutionReport(
                order_id=intent.order_id,
                broker_order_id="",
                symbol=intent.symbol,
                strategy_name=intent.strategy_name,
                side=intent.side,
                price=float(intent.price),
                quantity=float(intent.quantity),
                filled_quantity=0.0,
                average_fill_price=0.0,
                decision_timestamp=int(intent.decision_timestamp),
                send_timestamp=int(intent.decision_timestamp),
                exchange_receive_timestamp=int(intent.decision_timestamp),
                ack_timestamp=int(intent.decision_timestamp),
                updated_at_ns=int(intent.decision_timestamp),
                fill_timestamp=None,
                state=OrderLifecycleState.REJECTED,
                accepted=False,
                reason=f"risk_not_passed:{intent.risk_reason}",
                metadata=dict(intent.metadata),
            )
        sent_ns = time.time_ns()
        session = normalize_market_session(intent.session, fallback_timestamp_ns=int(intent.decision_timestamp))
        body = {
            "symbol": intent.symbol.upper(),
            "qty": f"{float(intent.quantity):.6f}".rstrip("0").rstrip("."),
            "side": intent.side.lower(),
            "type": intent.order_type,
            "limit_price": round(float(intent.price), 4),
            "time_in_force": "day",
            "extended_hours": bool(is_extended_session(session)),
            "client_order_id": intent.order_id,
        }
        response = request_json(
            f"{self.base_url}/v2/orders",
            headers=self._headers(),
            method="POST",
            body=body,
            timeout_seconds=self.request_timeout_seconds,
        )
        if response.status_code not in {200, 201} or not isinstance(response.payload, dict):
            return PaperExecutionReport(
                order_id=intent.order_id,
                broker_order_id="",
                symbol=intent.symbol,
                strategy_name=intent.strategy_name,
                side=intent.side,
                price=float(intent.price),
                quantity=float(intent.quantity),
                filled_quantity=0.0,
                average_fill_price=0.0,
                decision_timestamp=int(intent.decision_timestamp),
                send_timestamp=sent_ns,
                exchange_receive_timestamp=sent_ns,
                ack_timestamp=sent_ns,
                updated_at_ns=sent_ns,
                fill_timestamp=None,
                state=OrderLifecycleState.REJECTED,
                accepted=False,
                reason=response.message or "submit_failed",
                metadata={**intent.metadata, "session": session},
            )
        self._known_intents[intent.order_id] = intent
        return self._report_from_alpaca_order(response.payload, intent=intent, sent_ns=sent_ns)

    def cancel_order(self, broker_order_id: str, *, symbol: str | None = None) -> PaperExecutionReport:
        sent_ns = time.time_ns()
        response = request_json(
            f"{self.base_url}/v2/orders/{broker_order_id}",
            headers=self._headers(),
            method="DELETE",
            timeout_seconds=self.request_timeout_seconds,
        )
        if response.status_code not in {200, 204}:
            return PaperExecutionReport(
                order_id=broker_order_id,
                broker_order_id=broker_order_id,
                symbol=str(symbol or ""),
                strategy_name="",
                side="",
                price=0.0,
                quantity=0.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                decision_timestamp=sent_ns,
                send_timestamp=sent_ns,
                exchange_receive_timestamp=sent_ns,
                ack_timestamp=sent_ns,
                updated_at_ns=sent_ns,
                fill_timestamp=None,
                state=OrderLifecycleState.REJECTED,
                accepted=False,
                reason=response.message or "cancel_failed",
                metadata={},
            )
        payload = response.payload if isinstance(response.payload, dict) else {}
        return self._report_from_alpaca_order(payload, sent_ns=sent_ns)

    def sync_orders(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        params: dict[str, Any] = {"status": "all", "direction": "desc", "limit": 500}
        response = request_json(
            f"{self.base_url}/v2/orders",
            headers=self._headers(),
            params=params,
            timeout_seconds=self.request_timeout_seconds,
        )
        if response.status_code != 200 or not isinstance(response.payload, list):
            return []
        reports = [self._report_from_alpaca_order(item) for item in response.payload if isinstance(item, dict)]
        if symbols:
            wanted = {symbol.upper() for symbol in symbols}
            reports = [report for report in reports if report.symbol.upper() in wanted]
        tracked = set(self._known_intents)
        return [report for report in reports if report.order_id in tracked or report.broker_order_id in tracked or report.symbol.upper() in {symbol.upper() for symbol in (symbols or [])}]

    def flatten_positions(self, symbols: list[str] | None = None) -> list[PaperExecutionReport]:
        target_symbols = list(symbols or [])
        if not target_symbols:
            positions_response = request_json(
                f"{self.base_url}/v2/positions",
                headers=self._headers(),
                timeout_seconds=self.request_timeout_seconds,
            )
            if positions_response.status_code == 200 and isinstance(positions_response.payload, list):
                target_symbols = [str(item.get("symbol") or "").upper() for item in positions_response.payload if isinstance(item, dict)]
        reports: list[PaperExecutionReport] = []
        for symbol in target_symbols:
            sent_ns = time.time_ns()
            response = request_json(
                f"{self.base_url}/v2/positions/{symbol.upper()}",
                headers=self._headers(),
                method="DELETE",
                timeout_seconds=self.request_timeout_seconds,
            )
            if response.status_code in {200, 202} and isinstance(response.payload, dict):
                reports.append(self._report_from_alpaca_order(response.payload, sent_ns=sent_ns))
            else:
                reports.append(
                    PaperExecutionReport(
                        order_id=f"flatten-{symbol.upper()}",
                        broker_order_id="",
                        symbol=symbol.upper(),
                        strategy_name="flatten",
                        side="sell",
                        price=0.0,
                        quantity=0.0,
                        filled_quantity=0.0,
                        average_fill_price=0.0,
                        decision_timestamp=sent_ns,
                        send_timestamp=sent_ns,
                        exchange_receive_timestamp=sent_ns,
                        ack_timestamp=sent_ns,
                        updated_at_ns=sent_ns,
                        fill_timestamp=None,
                        state=OrderLifecycleState.REJECTED,
                        accepted=False,
                        reason=response.message or "flatten_failed",
                        metadata={},
                    )
                )
        return reports

    def _report_from_alpaca_order(
        self,
        payload: dict[str, Any],
        *,
        intent: PaperOrderIntent | None = None,
        sent_ns: int | None = None,
    ) -> PaperExecutionReport:
        client_order_id = str(payload.get("client_order_id") or (intent.order_id if intent else payload.get("id") or ""))
        cached_intent = intent or self._known_intents.get(client_order_id)
        decision_ts = int(cached_intent.decision_timestamp if cached_intent else time.time_ns())
        local_send_ns = int(sent_ns if sent_ns is not None else decision_ts)
        submitted_at_ns = parse_timestamp_ns(payload.get("submitted_at"), fallback_ns=local_send_ns)
        updated_at_ns = parse_timestamp_ns(payload.get("updated_at"), fallback_ns=submitted_at_ns)
        filled_at_ns = parse_timestamp_ns(payload.get("filled_at"), fallback_ns=updated_at_ns) if payload.get("filled_at") else None
        status = str(payload.get("status") or "new").strip().lower()
        state = self._map_state(status)
        symbol = str(payload.get("symbol") or (cached_intent.symbol if cached_intent else "")).upper()
        strategy_name = str((cached_intent.strategy_name if cached_intent else payload.get("strategy_name") or "") or "")
        report = PaperExecutionReport(
            order_id=client_order_id,
            broker_order_id=str(payload.get("id") or client_order_id),
            symbol=symbol,
            strategy_name=strategy_name,
            side=str(payload.get("side") or (cached_intent.side if cached_intent else "")).lower(),
            price=float(payload.get("limit_price") or (cached_intent.price if cached_intent else 0.0) or 0.0),
            quantity=float(payload.get("qty") or (cached_intent.quantity if cached_intent else 0.0) or 0.0),
            filled_quantity=float(payload.get("filled_qty") or 0.0),
            average_fill_price=float(payload.get("filled_avg_price") or payload.get("limit_price") or 0.0),
            decision_timestamp=decision_ts,
            send_timestamp=local_send_ns,
            exchange_receive_timestamp=submitted_at_ns,
            ack_timestamp=updated_at_ns,
            updated_at_ns=updated_at_ns,
            fill_timestamp=filled_at_ns,
            state=state,
            accepted=state != OrderLifecycleState.REJECTED,
            reason=status,
            metadata={
                "raw_status": status,
                "extended_hours": bool(payload.get("extended_hours", False)),
                "session": str((cached_intent.session if cached_intent else "regular") or "regular"),
            },
        )
        return report

    @staticmethod
    def _map_state(status: str) -> OrderLifecycleState:
        mapping = {
            "accepted": OrderLifecycleState.ACKED,
            "accepted_for_bidding": OrderLifecycleState.ACKED,
            "new": OrderLifecycleState.LIVE,
            "partially_filled": OrderLifecycleState.PARTIALLY_FILLED,
            "filled": OrderLifecycleState.FILLED,
            "done_for_day": OrderLifecycleState.EXPIRED,
            "canceled": OrderLifecycleState.CANCELED,
            "expired": OrderLifecycleState.EXPIRED,
            "rejected": OrderLifecycleState.REJECTED,
            "pending_cancel": OrderLifecycleState.CANCEL_REQUESTED,
            "pending_replace": OrderLifecycleState.CANCEL_REQUESTED,
        }
        return mapping.get(status, OrderLifecycleState.SENT)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
            "Accept": "application/json",
            "User-Agent": "hft-system/paper-execution",
        }

    def to_dict(self) -> dict[str, Any]:
        return {"base_url": self.base_url, "paper_only": True}
