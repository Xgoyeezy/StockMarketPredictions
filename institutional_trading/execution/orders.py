from __future__ import annotations
from dataclasses import dataclass
from institutional_trading.execution.broker import OrderRecord
from institutional_trading.models import OrderIntent, OrderState, OrderType, Session, utc_now
ALLOWED_TRANSITIONS = {OrderState.CREATED:{OrderState.RISK_REJECTED,OrderState.PENDING_SUBMIT}, OrderState.RISK_REJECTED:set(), OrderState.PENDING_SUBMIT:{OrderState.SUBMITTED,OrderState.RETRY_PENDING,OrderState.REJECTED,OrderState.FAILED}, OrderState.SUBMITTED:{OrderState.ACKNOWLEDGED,OrderState.RETRY_PENDING,OrderState.REJECTED,OrderState.FAILED}, OrderState.ACKNOWLEDGED:{OrderState.PARTIALLY_FILLED,OrderState.FILLED,OrderState.CANCEL_REQUESTED,OrderState.REJECTED}, OrderState.PARTIALLY_FILLED:{OrderState.PARTIALLY_FILLED,OrderState.FILLED,OrderState.CANCEL_REQUESTED,OrderState.REJECTED}, OrderState.CANCEL_REQUESTED:{OrderState.CANCELED,OrderState.REJECTED,OrderState.FAILED}, OrderState.RETRY_PENDING:{OrderState.PENDING_SUBMIT,OrderState.FAILED}, OrderState.REJECTED:{OrderState.RETRY_PENDING}, OrderState.FILLED:set(), OrderState.CANCELED:set(), OrderState.FAILED:set()}
class InvalidOrderTransition(ValueError): pass
@dataclass
class OrderStateMachine:
    clock: callable = utc_now
    def new_record(self, intent: OrderIntent, *, broker_order_id: str) -> OrderRecord:
        if intent.extended_hours and intent.order_type != OrderType.LIMIT: raise ValueError("Extended-hours orders must be limit orders.")
        if intent.session in {Session.PRE_MARKET, Session.AFTER_HOURS} and intent.order_type != OrderType.LIMIT: raise ValueError("Pre-market and after-hours orders must be limit orders.")
        now = self.clock(); rec = OrderRecord(intent, broker_order_id, created_at=now); rec.state_history.append((OrderState.CREATED, now, "created")); return rec
    def transition(self, record: OrderRecord, state: OrderState, *, reason: str) -> OrderRecord:
        if state not in ALLOWED_TRANSITIONS[record.state]: raise InvalidOrderTransition(f"Cannot transition order {record.broker_order_id} from {record.state.value} to {state.value}.")
        now = self.clock(); record.state = state; record.state_history.append((state, now, reason))
        if state == OrderState.SUBMITTED: record.submitted_at = now
        if state == OrderState.ACKNOWLEDGED: record.acknowledged_at = now
        if record.terminal: record.terminal_at = now
        return record
