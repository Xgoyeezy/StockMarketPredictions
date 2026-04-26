from __future__ import annotations
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from institutional_trading.accounts import AccountConfig, AllocationEngine, AllocationMode
from institutional_trading.execution import OrderStateMachine, PaperBrokerAdapter
from institutional_trading.models import AccountSnapshot, OrderIntent, OrderSide, OrderState, OrderType, Session, Signal
from institutional_trading.risk import KillSwitch, RiskEngine, RiskLimits
def signal(quantity=101): return Signal("sig-1","test","1","AAPL",OrderSide.BUY,quantity,100,0.8,datetime(2026,4,24,10,0,tzinfo=ZoneInfo("America/New_York")))
def intent(account_id="A", quantity=10, session=Session.REGULAR, order_type=OrderType.LIMIT): return OrderIntent(f"key-{account_id}-{quantity}-{session.value}-{order_type.value}", account_id, "AAPL", OrderSide.BUY, quantity, order_type, 100 if order_type == OrderType.LIMIT else None, session, session in {Session.PRE_MARKET, Session.AFTER_HOURS}, "test", "1", "sig-1", datetime(2026,4,24,10,0,tzinfo=ZoneInfo("America/New_York")))
class AllocationExecutionRiskTest(unittest.TestCase):
    def test_proportional_allocation_has_deterministic_residuals(self):
        plan = AllocationEngine(AllocationMode.PROPORTIONAL).allocate(signal(), [AccountConfig("A","A",allocation_weight=.60), AccountConfig("B","B",allocation_weight=.40)]); self.assertEqual(plan.quantity_for("A"),61); self.assertEqual(plan.quantity_for("B"),40)
    def test_fixed_allocation_and_partial_fill_distribution(self):
        engine = AllocationEngine(AllocationMode.FIXED); plan = engine.allocate(signal(80), [AccountConfig("A","A",fixed_quantity=50), AccountConfig("B","B",fixed_quantity=50)]); self.assertEqual(plan.quantity_for("A"),50); self.assertEqual(plan.quantity_for("B"),30); self.assertEqual(engine.allocate_partial_fill(41, plan), {"A":26,"B":15})
    def test_extended_hours_rejects_market_orders_before_submission(self):
        with self.assertRaises(ValueError): OrderStateMachine().new_record(intent(session=Session.AFTER_HOURS, order_type=OrderType.MARKET), broker_order_id="bad")
    def test_paper_broker_is_idempotent_and_handles_partial_fills(self):
        broker = PaperBrokerAdapter(); broker.connect(); order = broker.submit_order(intent(quantity=10)); self.assertIs(order, broker.submit_order(intent(quantity=10))); broker.fill_order(order.broker_order_id, quantity=4, price=100.1); self.assertEqual(order.state, OrderState.PARTIALLY_FILLED); broker.fill_order(order.broker_order_id, quantity=6, price=100.2); self.assertEqual(order.state, OrderState.FILLED); self.assertEqual(order.filled_quantity, 10)
    def test_risk_rejects_limits_and_kill_switch(self):
        engine = RiskEngine(RiskLimits(max_order_quantity=5,max_daily_loss=100), KillSwitch()); self.assertEqual(engine.validate_order(intent(account_id="A",quantity=4), [AccountSnapshot("A",100000,100000,daily_pnl=-101)]).reason, "max_daily_loss"); self.assertEqual(engine.validate_order(intent(account_id="A",quantity=6), [AccountSnapshot("A",100000,100000)]).reason, "max_order_quantity"); engine.kill_switch.trip_global("operator"); self.assertEqual(engine.validate_order(intent(account_id="A",quantity=1), [AccountSnapshot("A",100000,100000)]).reason, "global_kill_switch")
if __name__ == "__main__": unittest.main()
