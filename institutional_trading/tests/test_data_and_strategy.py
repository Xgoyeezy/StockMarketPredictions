from __future__ import annotations
import tempfile, unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from institutional_trading.data import FailoverMarketDataProvider, MarketDataNormalizer, StaticMarketDataProvider, classify_us_equity_session, read_market_replay, write_market_replay
from institutional_trading.models import AccountSnapshot, DataGranularity, PortfolioSnapshot, Session
from institutional_trading.strategies import MeanReversionStrategy
class DataAndStrategyTest(unittest.TestCase):
    def test_classifies_extended_hours_sessions(self):
        e = ZoneInfo("America/New_York"); self.assertEqual(classify_us_equity_session(datetime(2026,4,24,8,0,tzinfo=e)), Session.PRE_MARKET); self.assertEqual(classify_us_equity_session(datetime(2026,4,24,10,0,tzinfo=e)), Session.REGULAR); self.assertEqual(classify_us_equity_session(datetime(2026,4,24,17,0,tzinfo=e)), Session.AFTER_HOURS); self.assertEqual(classify_us_equity_session(datetime(2026,4,24,21,0,tzinfo=e)), Session.CLOSED)
    def test_normalizes_and_replays_minute_records(self):
        rows = [{"symbol":"aapl","timestamp":"2026-04-24T08:00:00-04:00","open":100,"high":101,"low":99,"close":100.5,"volume":1000}]; records = MarketDataNormalizer().normalize(rows, source="fixture", granularity=DataGranularity.MINUTE); self.assertEqual(records[0].symbol,"AAPL"); self.assertEqual(records[0].session, Session.PRE_MARKET)
        with tempfile.TemporaryDirectory() as tmp: self.assertEqual([r.to_record() for r in read_market_replay(write_market_replay(Path(tmp)/"market.jsonl", records))], [r.to_record() for r in records])
    def test_failover_provider_uses_fallback_when_primary_fails(self):
        records = MarketDataNormalizer().normalize([{"symbol":"AAPL","timestamp":"2026-04-24T10:00:00-04:00","open":100,"high":101,"low":99,"close":100}], source="fallback", granularity=DataGranularity.MINUTE); provider = FailoverMarketDataProvider([StaticMarketDataProvider([], provider_name="primary", fail=True), StaticMarketDataProvider(records, provider_name="fallback")]); self.assertEqual(len(provider.latest(symbol="AAPL", granularity=DataGranularity.MINUTE)), 1); self.assertTrue(provider.failure_log); self.assertEqual(provider.health().status.value, "degraded")
    def test_mean_reversion_strategy_is_stateless_and_versioned(self):
        rows = [{"symbol":"MSFT","timestamp":f"2026-04-24T10:0{i}:00-04:00","open":p,"high":p,"low":p,"close":p} for i,p in enumerate([100,100,100,100,95])]; records = MarketDataNormalizer().normalize(rows, source="fixture", granularity=DataGranularity.MINUTE); strategy = MeanReversionStrategy(lookback=5,zscore_threshold=1.0,target_quantity=25); portfolio = PortfolioSnapshot((AccountSnapshot("A1",100000,100000),)); first = [s.to_record() for s in strategy.generate_signals(market_records=records, portfolio=portfolio)]; second = [s.to_record() for s in strategy.generate_signals(market_records=records, portfolio=portfolio)]; self.assertEqual(first, second); self.assertEqual(first[0]["strategy_version"], "1.0.0")
if __name__ == "__main__": unittest.main()
