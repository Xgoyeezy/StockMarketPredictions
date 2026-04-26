from __future__ import annotations
from dataclasses import dataclass, field
@dataclass(frozen=True)
class RiskLimits:
    max_order_quantity: int = 1_000; max_position_size: int = 2_000; max_symbol_exposure: int = 5_000; max_gross_exposure: float = 500_000.0; max_daily_loss: float = 2_500.0; max_drawdown: float = 5_000.0; restricted_symbols: frozenset[str] = field(default_factory=frozenset)
    def __post_init__(self) -> None: object.__setattr__(self, "restricted_symbols", frozenset(s.upper() for s in self.restricted_symbols))
