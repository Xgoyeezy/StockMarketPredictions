from __future__ import annotations
import tempfile, unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from institutional_trading.accounts import AccountConfig, AllocationEngine
from institutional_trading.audit import HashChainedAuditLogger, read_replay_events
from institutional_trading.backtest import BacktestEngine
from institutional_trading.data import MarketDataNormalizer
from institutional_trading.execution import PaperBrokerAdapter
from institutional_trading.models import AuditEvent, DataGranularity, HealthStatus
from institutional_trading.risk import RiskEngine, RiskLimits
from institutional_trading.security import AccessDenied, RBACPolicy
from institutional_trading.service import TradingService
from institutional_trading.strategies import MeanReversionStrategy
class AuditSecurityServiceBacktestTest(unittest.TestCase):
    def test_audit_logger_hash_chain_replay_and_account_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"audit"/"events.jsonl"; logger = HashChainedAuditLogger(path); logger.append(AuditEvent("order_submitted","system",{"x":1},account_id="A",symbol="AAPL",order_id="O1")); logger.append(AuditEvent("fill","system",{"x":2},account_id="A",symbol="AAPL",order_id="O1")); self.assertTrue(logger.verify_chain()); self.assertEqual(len(logger.events_by_account("A")),2); self.assertEqual([e.sequence for e in read_replay_events(path)], [1,2])
    def test_rbac_restricts_trading_actions(self):
        policy = RBACPolicy(); self.assertTrue(policy.allowed(role="admin", action="submit_order"));
        with self.assertRaises(AccessDenied): policy.assert_allowed(role="viewer", action="submit_order")
    def test_service_start_kill_stop_writes_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)/"runtime"; service = TradingService(PaperBrokerAdapter(), RiskEngine(RiskLimits()), HashChainedAuditLogger(runtime/"audit"/"events.jsonl"), runtime); self.assertIn(service.start().status, {HealthStatus.HEALTHY, HealthStatus.DEGRADED}); self.assertEqual(service.kill("test").status, HealthStatus.FAILED); self.assertEqual(service.stop().status, HealthStatus.DEGRADED); self.assertFalse(service.status()["running"])
    def test_backtest_uses_same_strategy_interface_and_outputs_metrics(self):
        rows = [{"symbol":"AAPL","timestamp":datetime(2026,4,24,10,i,tzinfo=ZoneInfo("America/New_York")),"open":p,"high":p,"low":p,"close":p} for i,p in enumerate([100,100,100,100,95,96])]; records = MarketDataNormalizer().normalize(rows, source="fixture", granularity=DataGranularity.MINUTE); result = BacktestEngine(AllocationEngine(), RiskEngine(RiskLimits(max_order_quantity=1000)), partial_fill_ratio=.5).run(records=records, strategy=MeanReversionStrategy(lookback=5,zscore_threshold=1.0,target_quantity=20), accounts=[AccountConfig("A","A",allocation_weight=1.0)]); self.assertIn("sharpe", result.metrics); self.assertIn("max_drawdown", result.metrics); self.assertIn("win_rate", result.metrics); self.assertGreaterEqual(result.metrics["fills"],1); self.assertIn("A", result.per_account_pnl)
if __name__ == "__main__": unittest.main()
