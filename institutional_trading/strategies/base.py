from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Sequence
from institutional_trading.models import MarketRecord, PortfolioSnapshot, Signal
class StatelessStrategy(ABC):
    name: str; version: str
    @abstractmethod
    def generate_signals(self, *, market_records: Sequence[MarketRecord], portfolio: PortfolioSnapshot) -> Sequence[Signal]: raise NotImplementedError
