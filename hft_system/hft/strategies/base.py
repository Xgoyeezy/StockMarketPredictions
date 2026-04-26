from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from hft.features.fair_value import FairValueEstimate
from hft.features.microstructure import MicrostructureFeatureSnapshot
from hft.inventory.inventory import InventoryState
from hft.market_data.schemas import MarketEvent


@dataclass(frozen=True)
class QuoteInstruction:
    side: str
    price: float
    quantity: float
    quote_width: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CancelInstruction:
    order_id: str
    reason: str


@dataclass(frozen=True)
class StrategyHealthSnapshot:
    strategy_name: str
    enabled: bool
    risk_state: str
    detail: str
    timestamp_ns: int


class HFTStrategy(ABC):
    strategy_name: str

    @abstractmethod
    def on_market_event(self, event: MarketEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    def compute_features(self, book_state: Any) -> MicrostructureFeatureSnapshot:
        raise NotImplementedError

    @abstractmethod
    def estimate_fair_value(self, features: MicrostructureFeatureSnapshot) -> FairValueEstimate:
        raise NotImplementedError

    @abstractmethod
    def generate_quotes(self, features: MicrostructureFeatureSnapshot, inventory_state: InventoryState) -> list[QuoteInstruction]:
        raise NotImplementedError

    @abstractmethod
    def generate_cancels(self, active_orders: list[Any], market_state: Any) -> list[CancelInstruction]:
        raise NotImplementedError

    @abstractmethod
    def publish_risk_state(self) -> StrategyHealthSnapshot:
        raise NotImplementedError
