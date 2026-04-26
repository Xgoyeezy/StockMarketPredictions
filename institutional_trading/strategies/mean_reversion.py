from __future__ import annotations
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Sequence
from institutional_trading.models import MarketRecord, OrderSide, PortfolioSnapshot, Signal
@dataclass(frozen=True)
class MeanReversionStrategy:
    name: str = "mean_reversion_example"; version: str = "1.0.0"; lookback: int = 5; zscore_threshold: float = 1.0; target_quantity: int = 100
    def generate_signals(self, *, market_records: Sequence[MarketRecord], portfolio: PortfolioSnapshot) -> Sequence[Signal]:
        if len(market_records) < self.lookback: return []
        ordered = sorted(market_records, key=lambda r: (r.timestamp, r.sequence)); latest = ordered[-1]; window = [r.price for r in ordered[-self.lookback:]]; avg = mean(window); std = pstdev(window)
        if std <= 0: return []
        z = (latest.price - avg) / std
        if z >= self.zscore_threshold: side = OrderSide.SELL
        elif z <= -self.zscore_threshold: side = OrderSide.BUY
        else: return []
        sid = f"{self.name}:{self.version}:{latest.symbol}:{latest.timestamp.isoformat()}:{side.value}"
        return [Signal(sid, self.name, self.version, latest.symbol, side, self.target_quantity, latest.price, min(abs(z) / max(self.zscore_threshold * 3.0, 1.0), 1.0), latest.timestamp, {"lookback": self.lookback, "zscore": z, "mean": avg, "std": std, "session": latest.session.value})]
