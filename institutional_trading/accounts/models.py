from __future__ import annotations
from dataclasses import dataclass, field
@dataclass(frozen=True)
class AccountConfig:
    account_id: str; display_name: str; allocation_weight: float = 1.0; fixed_quantity: int = 0; max_position_size: int = 1_000; max_daily_loss: float = 1_000.0; restricted_symbols: frozenset[str] = field(default_factory=frozenset); enabled: bool = True
    def __post_init__(self) -> None:
        object.__setattr__(self, "restricted_symbols", frozenset(symbol.upper() for symbol in self.restricted_symbols))
        if self.allocation_weight < 0: raise ValueError("allocation_weight cannot be negative.")
        if self.fixed_quantity < 0: raise ValueError("fixed_quantity cannot be negative.")
