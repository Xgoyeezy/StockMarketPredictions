from __future__ import annotations
from dataclasses import dataclass, field
from institutional_trading.execution.broker import OrderRecord
from institutional_trading.execution.orders import OrderStateMachine
from institutional_trading.models import FillReport, HealthStatus, OrderIntent, OrderState, ServiceHealth, utc_now
@dataclass
class PaperBrokerAdapter:
    adapter_name: str = "paper"; reject_symbols: set[str] = field(default_factory=set); state_machine: OrderStateMachine = field(default_factory=OrderStateMachine); _orders_by_idempotency: dict[str, OrderRecord] = field(default_factory=dict); _orders_by_broker_id: dict[str, OrderRecord] = field(default_factory=dict); _fills: list[FillReport] = field(default_factory=list); _connected: bool = False
    def connect(self) -> ServiceHealth: self._connected = True; return self.health()
    def health(self) -> ServiceHealth:
        status = HealthStatus.HEALTHY if self._connected else HealthStatus.DEGRADED; return ServiceHealth(status, "broker.paper", "Paper broker connected." if self._connected else "Paper broker not connected.")
    def submit_order(self, intent: OrderIntent) -> OrderRecord:
        if intent.idempotency_key in self._orders_by_idempotency: return self._orders_by_idempotency[intent.idempotency_key]
        oid = f"PAPER-{len(self._orders_by_broker_id)+1:08d}"; rec = self.state_machine.new_record(intent, broker_order_id=oid); self.state_machine.transition(rec, OrderState.PENDING_SUBMIT, reason="ready")
        if intent.symbol in {s.upper() for s in self.reject_symbols}: self.state_machine.transition(rec, OrderState.REJECTED, reason="configured_reject_symbol")
        else: self.state_machine.transition(rec, OrderState.SUBMITTED, reason="paper_submit"); self.state_machine.transition(rec, OrderState.ACKNOWLEDGED, reason="paper_ack")
        self._orders_by_idempotency[intent.idempotency_key] = rec; self._orders_by_broker_id[oid] = rec; return rec
    def cancel_order(self, broker_order_id: str, *, reason: str) -> OrderRecord:
        rec = self._orders_by_broker_id[broker_order_id]
        if rec.terminal: return rec
        self.state_machine.transition(rec, OrderState.CANCEL_REQUESTED, reason=reason); self.state_machine.transition(rec, OrderState.CANCELED, reason="paper_cancel_ack"); return rec
    def fill_order(self, broker_order_id: str, *, quantity: int, price: float, liquidity_flag: str = "paper") -> FillReport:
        rec = self._orders_by_broker_id[broker_order_id]; qty = min(int(quantity), rec.remaining_quantity)
        if qty <= 0: raise ValueError("order has no remaining quantity.")
        fill = FillReport(rec.broker_order_id, rec.intent.account_id, rec.intent.symbol, rec.intent.side, qty, float(price), utc_now(), liquidity_flag)
        prev = rec.average_fill_price * rec.filled_quantity; rec.filled_quantity += qty; rec.average_fill_price = (prev + qty * float(price)) / rec.filled_quantity; rec.fill_reports.append(fill); self._fills.append(fill); self.state_machine.transition(rec, OrderState.FILLED if rec.remaining_quantity == 0 else OrderState.PARTIALLY_FILLED, reason="paper_fill"); return fill
    def list_open_orders(self) -> list[OrderRecord]: return [r for r in self._orders_by_broker_id.values() if not r.terminal]
    def fills(self) -> list[FillReport]: return list(self._fills)
