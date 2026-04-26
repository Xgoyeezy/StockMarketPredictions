from __future__ import annotations
from dataclasses import dataclass
from institutional_trading.models import FillReport
@dataclass(frozen=True)
class ReconciliationBreak:
    account_id: str; symbol: str; expected_position: int; broker_position: int; detail: str
@dataclass(frozen=True)
class ReconciliationReport:
    breaks: tuple[ReconciliationBreak, ...]
    @property
    def clean(self) -> bool: return not self.breaks
class ReconciliationService:
    def expected_positions(self, fills: list[FillReport]) -> dict[tuple[str, str], int]:
        positions = {}
        for fill in fills:
            key = (fill.account_id, fill.symbol); signed = fill.quantity if fill.side.value == "buy" else -fill.quantity; positions[key] = positions.get(key, 0) + signed
        return positions
    def reconcile(self, *, fills: list[FillReport], broker_positions: dict[tuple[str, str], int]) -> ReconciliationReport:
        expected = self.expected_positions(fills); breaks = []
        for account_id, symbol in sorted(set(expected) | set(broker_positions)):
            exp = expected.get((account_id, symbol), 0); brk = broker_positions.get((account_id, symbol), 0)
            if exp != brk: breaks.append(ReconciliationBreak(account_id, symbol, exp, brk, "Expected fill-derived position does not match broker position."))
        return ReconciliationReport(tuple(breaks))
