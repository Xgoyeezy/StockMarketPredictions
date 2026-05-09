from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.schemas import InternalBrokerPaperOrderRequest
from backend.services.exceptions import NotFoundError, ValidationServiceError
from backend.services.serialization import serialize_value
from institutional_trading.audit.logger import HashChainedAuditLogger
from institutional_trading.execution import PaperBrokerAdapter
from institutional_trading.execution.broker import OrderRecord
from institutional_trading.models import (
    AccountSnapshot,
    AuditEvent,
    FillReport,
    HealthStatus,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    RiskDecision,
    ServiceHealth,
    Session,
    utc_now,
)
from institutional_trading.risk import KillSwitch, RiskEngine, RiskLimits


DEFAULT_ROUTER_STATE_DIR = Path("runtime-logs") / "internal-broker-router"
INTERNAL_ACCOUNT_ID = "internal-paper"
INTERNAL_STARTING_CASH = 100_000.0


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _instrument_multiplier(instrument_type: str) -> int:
    return 100 if str(instrument_type or "").strip().lower() == "listed_option" else 1


def _route_for_instrument(instrument_type: str) -> dict[str, Any]:
    normalized = str(instrument_type or "equity").strip().lower()
    return {
        "asset_class": "listed_option" if normalized == "listed_option" else "equity",
        "broker_target": "internal_paper",
        "provider": "internal",
        "provider_configured": True,
        "execution_venue": "internal_simulator",
        "route_kind": "internal_simulator",
        "detail": "Internal simulator is the source of control; no external broker API is queried in v1.",
    }


@dataclass
class InternalPosition:
    symbol: str
    instrument_type: str
    quantity: int = 0
    average_price: float = 0.0
    multiplier: int = 1
    updated_at: str = field(default_factory=_utc_iso)

    @property
    def market_value(self) -> float:
        return float(self.quantity * self.average_price * self.multiplier)

    def to_record(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "instrument_type": self.instrument_type,
            "quantity": self.quantity,
            "average_price": self.average_price,
            "multiplier": self.multiplier,
            "market_value": self.market_value,
            "updated_at": self.updated_at,
        }


class InternalPaperBrokerRouterService:
    def __init__(self, *, state_dir: Path | str = DEFAULT_ROUTER_STATE_DIR) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.audit_logger = HashChainedAuditLogger(
            self.state_dir / "audit.jsonl",
            sqlite_path=self.state_dir / "audit.sqlite3",
        )
        self.broker = PaperBrokerAdapter()
        self.broker.connect()
        self.kill_switch = KillSwitch()
        self.risk_engine = RiskEngine(
            RiskLimits(
                max_order_quantity=2_000,
                max_position_size=5_000,
                max_symbol_exposure=10_000,
                max_gross_exposure=1_000_000.0,
                max_daily_loss=5_000.0,
                max_drawdown=10_000.0,
            ),
            self.kill_switch,
        )
        self._lock = threading.RLock()
        self._cash = INTERNAL_STARTING_CASH
        self._positions: dict[str, InternalPosition] = {}
        self._routes_by_order_id: dict[str, dict[str, Any]] = {}
        self._rejected_orders: list[dict[str, Any]] = []
        self._last_sync_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            internal_balance = self._internal_balance_snapshot()
            combined = self._combined_balance_snapshot(internal_balance)
            health = self._health_snapshot()
            return serialize_value(
                {
                    "status": health["status"],
                    "mode": "internal_paper",
                    "service_label": "Internal paper execution router",
                    "execution_router_mode": "internal_paper",
                    "deprecated_alias": "internal-broker-router",
                    "broker_mode": "internal_paper",
                    "paper_only": True,
                    "regulated_broker_dealer": False,
                    "real_money_execution_enabled": False,
                    "licensed_realtime_options_data": False,
                    "custody_and_execution_note": (
                        "This is an internal paper execution simulator only. It does not provide custody, "
                        "account statements, withdrawals, or real-money execution."
                    ),
                    "health": health,
                    "routing": {
                        "equities": "internal_paper",
                        "options": "internal_paper",
                        "options_data": "free_delayed",
                        "execution_venue": "internal_simulator",
                        "live_routing_enabled": False,
                    },
                    "balances": {
                        "internal_simulated": internal_balance,
                        "alpaca_paper": self._disabled_external_balance("alpaca"),
                        "tradier_paper": self._disabled_external_balance("tradier"),
                        "combined_paper": combined,
                    },
                    "orders": {
                        "open": [self._order_to_record(order) for order in self.broker.list_open_orders()],
                        "rejected": list(self._rejected_orders[-20:]),
                        "recent_fills": [self._fill_to_record(fill) for fill in self.broker.fills()[-20:]],
                    },
                    "positions": [position.to_record() for position in self._positions.values() if position.quantity],
                    "audit": {
                        "hash_chain_valid": self._audit_chain_valid(),
                        "latest_events": self.list_audit_events(limit=10),
                    },
                    "last_sync_at": self._last_sync_at,
                    "updated_at": _utc_iso(),
                }
            )

    def list_open_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._order_to_record(order) for order in self.broker.list_open_orders()]

    def list_fills(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return [self._fill_to_record(fill) for fill in self.broker.fills()[-limit:]]

    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        path = self.state_dir / "audit.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-max(int(limit), 1):]

    def _audit_chain_valid(self) -> bool:
        path = self.state_dir / "audit.jsonl"
        return True if not path.exists() else self.audit_logger.verify_chain()

    def sync(self) -> dict[str, Any]:
        with self._lock:
            self._last_sync_at = _utc_iso()
            self._append_audit(
                "internal_broker_router.synced",
                {
                    "open_order_count": len(self.broker.list_open_orders()),
                    "fill_count": len(self.broker.fills()),
                    "position_count": len([item for item in self._positions.values() if item.quantity]),
                },
            )
            return self.snapshot()

    def submit_order(self, payload: InternalBrokerPaperOrderRequest) -> dict[str, Any]:
        with self._lock:
            if payload.execution_mode != "paper":
                return self._reject_payload(
                    payload,
                    reason="live_routing_disabled",
                    detail="Internal paper execution router v1 is paper-only and rejects live order intent.",
                )

            if payload.instrument_type == "listed_option" and (
                payload.extended_hours or payload.session != "regular"
            ):
                return self._reject_payload(
                    payload,
                    reason="options_regular_session_only",
                    detail="Listed options stay locked to regular-session paper routing in v1.",
                )

            if payload.side == "sell":
                current_quantity = self._positions.get(payload.symbol, InternalPosition(payload.symbol, payload.instrument_type)).quantity
                if current_quantity < payload.quantity:
                    return self._reject_payload(
                        payload,
                        reason="sell_requires_existing_position",
                        detail="Internal paper router v1 only permits sell orders that close an existing simulated position.",
                    )

            route = _route_for_instrument(payload.instrument_type)
            intent = self._build_order_intent(payload)
            self._append_audit(
                "internal_broker_router.intent_received",
                {"intent": intent.to_record(), "route": route},
                account_id=intent.account_id,
                symbol=intent.symbol,
            )

            risk_decision = self._validate_risk(intent)
            self._append_audit(
                "internal_broker_router.risk_decision",
                risk_decision.to_record(),
                account_id=intent.account_id,
                symbol=intent.symbol,
            )
            if not risk_decision.allowed:
                return self._reject_intent(intent, route, risk_decision)

            try:
                order = self.broker.submit_order(intent)
            except ValueError as exc:
                decision = RiskDecision(
                    allowed=False,
                    reason="order_validation_failed",
                    detail=str(exc),
                    account_id=intent.account_id,
                    symbol=intent.symbol,
                )
                return self._reject_intent(intent, route, decision)

            self._routes_by_order_id[order.broker_order_id] = route
            order.risk_decision = risk_decision
            self._append_audit(
                "internal_broker_router.route_decision",
                {"broker_order_id": order.broker_order_id, "route": route},
                account_id=intent.account_id,
                symbol=intent.symbol,
                order_id=order.broker_order_id,
            )
            self._append_audit(
                "internal_broker_router.order_submitted",
                self._order_to_record(order),
                account_id=intent.account_id,
                symbol=intent.symbol,
                order_id=order.broker_order_id,
            )

            should_fill = bool(payload.simulate_fill) if payload.simulate_fill is not None else payload.order_type == "market"
            fill_record: dict[str, Any] | None = None
            if should_fill and not order.terminal:
                fill_price = payload.reference_price or payload.limit_price
                if fill_price is None or fill_price <= 0:
                    self.cancel_order(order.broker_order_id, reason="missing_fill_reference_price")
                    raise ValidationServiceError("A positive reference_price or limit_price is required to simulate a fill.")
                fill = self.broker.fill_order(order.broker_order_id, quantity=payload.quantity, price=float(fill_price))
                self._apply_fill(fill, payload.instrument_type)
                fill_record = self._fill_to_record(fill)
                self._append_audit(
                    "internal_broker_router.order_filled",
                    fill_record,
                    account_id=intent.account_id,
                    symbol=intent.symbol,
                    order_id=order.broker_order_id,
                )

            return {
                "accepted": True,
                "order": self._order_to_record(order),
                "fill": fill_record,
                "route": route,
                "balances": {
                    "internal_simulated": self._internal_balance_snapshot(),
                },
            }

    def cancel_order(self, broker_order_id: str, *, reason: str = "operator_cancel") -> dict[str, Any]:
        with self._lock:
            normalized = str(broker_order_id or "").strip()
            if not normalized:
                raise ValidationServiceError("A broker_order_id is required.")
            try:
                order = self.broker.cancel_order(normalized, reason=reason)
            except KeyError as exc:
                raise NotFoundError("Internal paper order was not found.") from exc
            record = self._order_to_record(order)
            self._append_audit(
                "internal_broker_router.order_canceled",
                {"reason": reason, "order": record},
                account_id=order.intent.account_id,
                symbol=order.intent.symbol,
                order_id=order.broker_order_id,
            )
            return {"canceled": True, "order": record, "route": self._routes_by_order_id.get(order.broker_order_id)}

    def _build_order_intent(self, payload: InternalBrokerPaperOrderRequest) -> OrderIntent:
        order_type = OrderType.MARKET if payload.order_type == "market" else OrderType.LIMIT
        session = Session(payload.session)
        idempotency_key = payload.idempotency_key or f"internal-router-{uuid.uuid4()}"
        signal_id = payload.signal_id or f"manual-{payload.symbol}-{uuid.uuid4().hex[:10]}"
        return OrderIntent(
            idempotency_key=idempotency_key,
            account_id=payload.account_id or INTERNAL_ACCOUNT_ID,
            symbol=payload.symbol,
            side=OrderSide.SELL if payload.side == "sell" else OrderSide.BUY,
            quantity=int(payload.quantity),
            order_type=order_type,
            limit_price=float(payload.limit_price) if order_type == OrderType.LIMIT else None,
            session=session,
            extended_hours=bool(payload.extended_hours),
            strategy_name=payload.strategy_name or "manual",
            strategy_version=payload.strategy_version or "1",
            signal_id=signal_id,
            created_at=utc_now(),
            decision_context={
                "instrument_type": payload.instrument_type,
                "paper_only": True,
                "reference_price": payload.reference_price,
            },
        )

    def _validate_risk(self, intent: OrderIntent) -> RiskDecision:
        return self.risk_engine.validate_order(intent, [self._account_snapshot(intent.account_id)])

    def _account_snapshot(self, account_id: str = INTERNAL_ACCOUNT_ID) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=account_id,
            equity=self._internal_equity(),
            cash=self._cash,
            positions={symbol: position.quantity for symbol, position in self._positions.items()},
            peak_equity=max(INTERNAL_STARTING_CASH, self._internal_equity()),
        )

    def _internal_equity(self) -> float:
        return float(self._cash + sum(position.market_value for position in self._positions.values()))

    def _internal_balance_snapshot(self) -> dict[str, Any]:
        equity = self._internal_equity()
        return {
            "provider": "internal_simulator",
            "equity": equity,
            "cash": self._cash,
            "buying_power": max(self._cash, 0.0),
            "option_buying_power": max(self._cash, 0.0),
            "status": "ready",
            "detail": "Internal simulated paper account is ready.",
            "last_refreshed_at": _utc_iso(),
            "position_market_value": equity - self._cash,
        }

    @staticmethod
    def _combined_balance_snapshot(internal_balance: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": "internal_paper",
            "equity": internal_balance.get("equity"),
            "cash": internal_balance.get("cash"),
            "buying_power": internal_balance.get("buying_power"),
            "option_buying_power": internal_balance.get("option_buying_power"),
            "status": "ready",
            "detail": "Internal-only paper total. This is not withdrawable or transferable cash.",
            "last_refreshed_at": _utc_iso(),
        }

    @staticmethod
    def _disabled_external_balance(provider: str) -> dict[str, Any]:
        return {
            "provider": provider,
            "equity": None,
            "cash": None,
            "buying_power": None,
            "option_buying_power": None,
            "status": "disabled",
            "detail": f"{provider.title()} paper is deprecated in internal-paper mode and is not queried.",
            "last_refreshed_at": None,
        }

    def _health_snapshot(self) -> dict[str, Any]:
        broker_health = self.broker.health()
        status = HealthStatus.FAILED if self.kill_switch.enabled else broker_health.status
        detail = (
            f"Kill switch active: {self.kill_switch.reason}"
            if self.kill_switch.enabled
            else "Internal paper broker/router is internal-only and paper-only."
        )
        return ServiceHealth(status, "internal_broker_router", detail, metrics={
            "open_order_count": len(self.broker.list_open_orders()),
            "fill_count": len(self.broker.fills()),
            "rejected_order_count": len(self._rejected_orders),
            "live_routing_enabled": False,
        }).to_record()

    def _apply_fill(self, fill: FillReport, instrument_type: str) -> None:
        multiplier = _instrument_multiplier(instrument_type)
        signed_quantity = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
        position = self._positions.get(fill.symbol)
        if position is None:
            position = InternalPosition(fill.symbol, instrument_type, multiplier=multiplier)
            self._positions[fill.symbol] = position
        previous_quantity = position.quantity
        previous_cost = position.average_price * previous_quantity
        next_quantity = previous_quantity + signed_quantity
        notional = fill.quantity * fill.price * multiplier
        self._cash += -notional if fill.side == OrderSide.BUY else notional
        if next_quantity <= 0:
            position.quantity = next_quantity
            position.average_price = 0.0 if next_quantity == 0 else fill.price
        elif fill.side == OrderSide.BUY:
            position.quantity = next_quantity
            position.average_price = (previous_cost + fill.quantity * fill.price) / next_quantity
        else:
            position.quantity = next_quantity
        position.updated_at = _utc_iso()

    def _reject_payload(
        self,
        payload: InternalBrokerPaperOrderRequest,
        *,
        reason: str,
        detail: str,
    ) -> dict[str, Any]:
        route = _route_for_instrument(payload.instrument_type)
        rejection = {
            "accepted": False,
            "reason": reason,
            "detail": detail,
            "symbol": payload.symbol,
            "instrument_type": payload.instrument_type,
            "route": route,
            "created_at": _utc_iso(),
        }
        self._rejected_orders.append(rejection)
        self._append_audit("internal_broker_router.order_rejected", rejection, symbol=payload.symbol)
        return rejection

    def _reject_intent(
        self,
        intent: OrderIntent,
        route: dict[str, Any],
        decision: RiskDecision,
    ) -> dict[str, Any]:
        rejection = {
            "accepted": False,
            "reason": decision.reason,
            "detail": decision.detail,
            "symbol": intent.symbol,
            "account_id": intent.account_id,
            "instrument_type": intent.decision_context.get("instrument_type"),
            "route": route,
            "risk_decision": decision.to_record(),
            "created_at": _utc_iso(),
        }
        self._rejected_orders.append(rejection)
        self._append_audit(
            "internal_broker_router.order_rejected",
            rejection,
            account_id=intent.account_id,
            symbol=intent.symbol,
        )
        return rejection

    def _order_to_record(self, order: OrderRecord) -> dict[str, Any]:
        return {
            "idempotency_key": order.intent.idempotency_key,
            "broker_order_id": order.broker_order_id,
            "account_id": order.intent.account_id,
            "symbol": order.intent.symbol,
            "side": _enum_value(order.intent.side),
            "quantity": order.intent.quantity,
            "order_type": _enum_value(order.intent.order_type),
            "limit_price": order.intent.limit_price,
            "session": _enum_value(order.intent.session),
            "extended_hours": order.intent.extended_hours,
            "strategy_name": order.intent.strategy_name,
            "strategy_version": order.intent.strategy_version,
            "signal_id": order.intent.signal_id,
            "state": _enum_value(order.state),
            "terminal": order.terminal,
            "filled_quantity": order.filled_quantity,
            "remaining_quantity": order.remaining_quantity,
            "average_fill_price": order.average_fill_price,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "acknowledged_at": order.acknowledged_at.isoformat() if order.acknowledged_at else None,
            "terminal_at": order.terminal_at.isoformat() if order.terminal_at else None,
            "route": self._routes_by_order_id.get(order.broker_order_id),
            "risk_decision": order.risk_decision.to_record() if order.risk_decision else None,
            "state_history": [
                {"state": _enum_value(state), "timestamp": timestamp.isoformat(), "reason": reason}
                for state, timestamp, reason in order.state_history
            ],
        }

    def _fill_to_record(self, fill: FillReport) -> dict[str, Any]:
        record = fill.to_record()
        record["notional"] = fill.quantity * fill.price * _instrument_multiplier(
            self._positions.get(fill.symbol, InternalPosition(fill.symbol, "equity")).instrument_type
        )
        return record

    def _append_audit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        account_id: str | None = None,
        symbol: str | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        return self.audit_logger.append(
            AuditEvent(
                event_type=event_type,
                actor="internal_broker_router",
                payload=serialize_value(payload),
                account_id=account_id,
                symbol=symbol,
                order_id=order_id,
            )
        )


_SERVICE: InternalPaperBrokerRouterService | None = None
_SERVICE_LOCK = threading.Lock()


def get_internal_broker_router_service() -> InternalPaperBrokerRouterService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = InternalPaperBrokerRouterService()
    return _SERVICE


def get_internal_broker_router_snapshot() -> dict[str, Any]:
    return get_internal_broker_router_service().snapshot()


def list_internal_broker_router_orders() -> dict[str, Any]:
    service = get_internal_broker_router_service()
    return {
        "items": service.list_open_orders(),
        "count": len(service.list_open_orders()),
    }


def list_internal_broker_router_fills(limit: int = 100) -> dict[str, Any]:
    items = get_internal_broker_router_service().list_fills(limit=limit)
    return {"items": items, "count": len(items)}


def list_internal_broker_router_audit(limit: int = 100) -> dict[str, Any]:
    items = get_internal_broker_router_service().list_audit_events(limit=limit)
    return {"items": items, "count": len(items)}


def submit_internal_broker_router_order(payload: InternalBrokerPaperOrderRequest) -> dict[str, Any]:
    return get_internal_broker_router_service().submit_order(payload)


def cancel_internal_broker_router_order(broker_order_id: str) -> dict[str, Any]:
    return get_internal_broker_router_service().cancel_order(broker_order_id)


def sync_internal_broker_router() -> dict[str, Any]:
    return get_internal_broker_router_service().sync()
