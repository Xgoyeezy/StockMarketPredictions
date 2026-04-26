from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from institutional_trading.accounts.models import AccountConfig
from institutional_trading.models import AllocationEntry, AllocationPlan, Signal
class AllocationMode(str, Enum):
    PROPORTIONAL = "proportional"; FIXED = "fixed"
@dataclass
class AllocationEngine:
    mode: AllocationMode = AllocationMode.PROPORTIONAL
    def allocate(self, signal: Signal, accounts: list[AccountConfig]) -> AllocationPlan:
        eligible = [a for a in accounts if a.enabled and signal.symbol not in a.restricted_symbols]
        entries = self._fixed(signal, eligible) if self.mode == AllocationMode.FIXED else self._proportional(signal, eligible)
        return AllocationPlan(signal.signal_id, signal.symbol, signal.side, signal.target_quantity, tuple(e for e in entries if e.quantity > 0), self.mode.value)
    def allocate_partial_fill(self, filled_quantity: int, plan: AllocationPlan) -> dict[str, int]:
        planned_total = sum(e.quantity for e in plan.entries)
        if planned_total <= 0: return {e.account_id: 0 for e in plan.entries}
        filled_quantity = min(max(int(filled_quantity), 0), planned_total)
        exact = [(e.account_id, filled_quantity * e.quantity / planned_total) for e in plan.entries]
        output = {account_id: int(value) for account_id, value in exact}
        residual = filled_quantity - sum(output.values())
        for account_id, _ in sorted(exact, key=lambda item: (-(item[1] - int(item[1])), item[0]))[:residual]: output[account_id] += 1
        return output
    def _fixed(self, signal: Signal, accounts: list[AccountConfig]) -> list[AllocationEntry]:
        remaining = signal.target_quantity; entries = []
        for account in sorted(accounts, key=lambda item: item.account_id):
            qty = min(account.fixed_quantity, remaining); remaining -= qty; entries.append(AllocationEntry(account.account_id, qty, account.allocation_weight, "fixed_size"))
            if remaining <= 0: break
        return entries
    def _proportional(self, signal: Signal, accounts: list[AccountConfig]) -> list[AllocationEntry]:
        if not accounts: return []
        raw_total = sum(max(a.allocation_weight, 0.0) for a in accounts)
        weights = {a.account_id: (max(a.allocation_weight, 0.0) if raw_total > 0 else 1.0) for a in accounts}
        total_weight = sum(weights.values())
        exact = [(a, signal.target_quantity * weights[a.account_id] / total_weight) for a in accounts]
        output = {a.account_id: int(qty) for a, qty in exact}
        residual = signal.target_quantity - sum(output.values())
        for a, _ in sorted(exact, key=lambda item: (-(item[1] - int(item[1])), item[0].account_id))[:residual]: output[a.account_id] += 1
        return [AllocationEntry(a.account_id, output[a.account_id], weights[a.account_id], "proportional_weight") for a, _ in exact]
