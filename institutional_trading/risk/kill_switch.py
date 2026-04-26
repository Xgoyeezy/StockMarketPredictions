from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from institutional_trading.models import utc_now
@dataclass
class KillSwitch:
    enabled: bool = False; reason: str = ""; tripped_at: datetime | None = None; disabled_symbols: set[str] = field(default_factory=set); disabled_accounts: set[str] = field(default_factory=set)
    def trip_global(self, reason: str) -> None: self.enabled = True; self.reason = reason; self.tripped_at = utc_now()
    def reset_global(self) -> None: self.enabled = False; self.reason = ""; self.tripped_at = None
    def symbol_allowed(self, symbol: str) -> bool: return symbol.upper() not in self.disabled_symbols
    def account_allowed(self, account_id: str) -> bool: return account_id not in self.disabled_accounts
