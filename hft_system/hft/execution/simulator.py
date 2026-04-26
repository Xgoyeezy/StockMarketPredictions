from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hft.execution.order_state import OrderLifecycleState, SimulatedFill, SimulatedOrder
from hft.execution.queue_model import QueuePositionModel
from hft.latency.model import LatencyModel, LatencyProfile
from hft.market_data.schemas import MarketEvent
from hft.market_data.sessions import is_extended_session, normalize_market_session
from hft.optimization.types import QueueCalibrationArtifact
from hft.risk.checks import HFTRiskEngine, RiskDecision
from hft.utils.ids import NamedIdPool


class ExecutionSimulator:
    def __init__(
        self,
        *,
        risk_engine: HFTRiskEngine,
        latency_model: LatencyModel,
        maker_rebate_per_share: float = 0.0015,
        taker_fee_per_share: float = 0.0030,
        seed: int = 7,
        queue_calibration: QueueCalibrationArtifact | None = None,
    ):
        self.risk_engine = risk_engine
        self.latency_model = latency_model
        self.ids = NamedIdPool()
        self.active_orders: dict[str, SimulatedOrder] = {}
        self.order_history: list[SimulatedOrder] = []
        self.fills: list[SimulatedFill] = []
        self.rejections: list[dict[str, Any]] = []
        self.rng = random.Random(seed)
        self.queue_model = QueuePositionModel(self.rng, calibration=queue_calibration or QueueCalibrationArtifact())
        self.maker_rebate_per_share = float(maker_rebate_per_share)
        self.taker_fee_per_share = float(taker_fee_per_share)

    def submit_order(self, order: SimulatedOrder) -> tuple[RiskDecision, SimulatedOrder]:
        decision = self.risk_engine.validate_order(order)
        self.risk_engine.record_message(order.decision_timestamp)
        if not decision.allowed:
            rejected = order
            rejected.state = OrderLifecycleState.REJECTED
            self.rejections.append({"order_id": order.order_id, "reason": decision.reason, "detail": decision.detail})
            return decision, rejected
        order.state = OrderLifecycleState.SENT
        order.state = OrderLifecycleState.ACKED
        order.state = OrderLifecycleState.LIVE
        self.active_orders[order.order_id] = order
        self.order_history.append(order)
        self.risk_engine.update_runtime_state(outstanding_orders=len(self.active_orders))
        return decision, order

    def create_limit_order(
        self,
        *,
        symbol: str,
        strategy_name: str,
        side: str,
        price: float,
        quantity: float,
        decision_timestamp: int,
        quote_width: float,
        metadata: dict[str, Any] | None = None,
    ) -> SimulatedOrder:
        submit_latency = self.latency_model.sample_ns("order_submit_ns")
        ack_latency = self.latency_model.sample_ns("exchange_ack_ns")
        send_ts = int(decision_timestamp) + submit_latency
        exchange_ts = send_ts
        ack_ts = exchange_ts + ack_latency
        order_metadata = dict(metadata or {})
        session = normalize_market_session(order_metadata.get("session"), fallback_timestamp_ns=int(decision_timestamp))
        order_metadata["session"] = session
        order_metadata["extended_hours"] = is_extended_session(session)
        return SimulatedOrder(
            order_id=self.ids.next("order"),
            symbol=symbol.upper(),
            strategy_name=strategy_name,
            side=side.lower(),
            price=float(price),
            quantity=float(quantity),
            decision_timestamp=int(decision_timestamp),
            send_timestamp=send_ts,
            exchange_receive_timestamp=exchange_ts,
            ack_timestamp=ack_ts,
            quote_width=float(quote_width),
            metadata=order_metadata,
        )

    def cancel_order(self, order_id: str, *, timestamp_ns: int) -> RiskDecision:
        self.risk_engine.record_cancel(timestamp_ns)
        decision = self.risk_engine.validate_cancel({"order_id": order_id}, self.risk_engine.state)
        if not decision.allowed:
            self.risk_engine.state.rejected_cancel_count += 1
            return decision
        order = self.active_orders.get(order_id)
        if order is None:
            return RiskDecision(False, "missing_order", "Active order not found.")
        order.state = OrderLifecycleState.CANCEL_REQUESTED
        cancel_latency = self.latency_model.sample_ns("cancel_ns")
        self.risk_engine.update_runtime_state(last_update_ts_ns=timestamp_ns + cancel_latency)
        order.state = OrderLifecycleState.CANCELED
        self.active_orders.pop(order_id, None)
        self.risk_engine.update_runtime_state(outstanding_orders=len(self.active_orders))
        return decision

    def modify_order(self, order_id: str, *, new_price: float, new_quantity: float) -> SimulatedOrder | None:
        order = self.active_orders.get(order_id)
        if order is None:
            return None
        order.price = float(new_price)
        order.quantity = float(new_quantity)
        return order

    def process_market_event(
        self,
        event: MarketEvent,
        *,
        best_bid: float,
        best_ask: float,
        visible_depth_by_price: dict[float, float] | None = None,
        imbalance: float = 0.0,
    ) -> list[SimulatedFill]:
        visible_depth_by_price = visible_depth_by_price or {}
        new_fills: list[SimulatedFill] = []
        mid_price = ((best_bid + best_ask) / 2.0) if best_bid and best_ask else (best_bid or best_ask or 0.0)
        spread_bps = (((best_ask - best_bid) / mid_price) * 10_000.0) if mid_price > 0 and best_ask and best_bid else 0.0
        for order in list(self.active_orders.values()):
            if event.receive_ts_ns < order.ack_timestamp:
                continue
            crossing = (
                (order.side == "buy" and event.event_type == "trade" and event.side == "sell" and event.price <= order.price)
                or (order.side == "sell" and event.event_type == "trade" and event.side == "buy" and event.price >= order.price)
            )
            if not crossing:
                continue
            visible_depth = float(visible_depth_by_price.get(order.price, event.size))
            fill_ratio = self.queue_model.fill_ratio(
                visible_depth=visible_depth,
                incoming_trade_size=float(event.size),
                order_size=order.remaining_quantity,
                spread_bps=spread_bps,
                imbalance=imbalance,
                latency_bucket=float((order.exchange_receive_timestamp - order.decision_timestamp) / 1_000_000.0),
                quote_age_ns=max(event.receive_ts_ns - order.ack_timestamp, 0),
            )
            fill_qty = min(order.remaining_quantity, max(order.remaining_quantity * fill_ratio, 0.0))
            if fill_qty <= 0:
                continue
            fill_ts = event.receive_ts_ns + self.latency_model.sample_ns("fill_ns")
            fill = SimulatedFill(
                order_id=order.order_id,
                symbol=order.symbol,
                strategy_name=order.strategy_name,
                side=order.side,
                price=order.price,
                quantity=fill_qty,
                fill_timestamp=fill_ts,
                liquidity_flag="maker",
            )
            order.filled_quantity += fill_qty
            order.fill_timestamp = fill_ts
            order.state = OrderLifecycleState.FILLED if order.remaining_quantity <= 1e-9 else OrderLifecycleState.PARTIALLY_FILLED
            new_fills.append(fill)
            self.fills.append(fill)
            if order.state == OrderLifecycleState.FILLED:
                self.active_orders.pop(order.order_id, None)
        self.risk_engine.update_runtime_state(outstanding_orders=len(self.active_orders))
        return new_fills

    def get_active_orders(self) -> list[SimulatedOrder]:
        return list(self.active_orders.values())

    def get_orders(self) -> list[SimulatedOrder]:
        return list(self.order_history)

    def get_fills(self) -> list[SimulatedFill]:
        return list(self.fills)

    def get_latency_summary(self) -> dict[str, float]:
        orders = self.get_orders()
        if not orders:
            return {
                "orders": 0.0,
                "avg_decision_to_send_ns": 0.0,
                "avg_send_to_exchange_receive_ns": 0.0,
                "avg_exchange_receive_to_ack_ns": 0.0,
                "avg_ack_to_fill_ns": 0.0,
            }
        decision_to_send = [max(order.send_timestamp - order.decision_timestamp, 0) for order in orders]
        send_to_exchange = [max(order.exchange_receive_timestamp - order.send_timestamp, 0) for order in orders]
        exchange_to_ack = [max(order.ack_timestamp - order.exchange_receive_timestamp, 0) for order in orders]
        ack_to_fill = [max(int(order.fill_timestamp or order.ack_timestamp) - order.ack_timestamp, 0) for order in orders if order.fill_timestamp is not None]
        return {
            "orders": float(len(orders)),
            "avg_decision_to_send_ns": float(sum(decision_to_send) / len(decision_to_send)),
            "avg_send_to_exchange_receive_ns": float(sum(send_to_exchange) / len(send_to_exchange)),
            "avg_exchange_receive_to_ack_ns": float(sum(exchange_to_ack) / len(exchange_to_ack)),
            "avg_ack_to_fill_ns": float(sum(ack_to_fill) / len(ack_to_fill)) if ack_to_fill else 0.0,
        }
