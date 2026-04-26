from __future__ import annotations
from dataclasses import dataclass, field
from institutional_trading.models import AccountSnapshot, OrderIntent, OrderSide, RiskDecision
from institutional_trading.risk.kill_switch import KillSwitch
from institutional_trading.risk.limits import RiskLimits
@dataclass
class RiskEngine:
    limits: RiskLimits; kill_switch: KillSwitch = field(default_factory=KillSwitch)
    def validate_order(self, intent: OrderIntent, accounts: list[AccountSnapshot]) -> RiskDecision:
        account = next((a for a in accounts if a.account_id == intent.account_id), None)
        if account is None: return RiskDecision(False, "unknown_account", "Account was not present in the risk snapshot.", intent.account_id, intent.symbol)
        if self.kill_switch.enabled: return RiskDecision(False, "global_kill_switch", self.kill_switch.reason or "Global kill switch is active.", intent.account_id, intent.symbol)
        if not self.kill_switch.account_allowed(intent.account_id): return RiskDecision(False, "account_disabled", "Account is disabled by kill switch.", intent.account_id, intent.symbol)
        if not self.kill_switch.symbol_allowed(intent.symbol): return RiskDecision(False, "symbol_disabled", "Symbol is disabled by kill switch.", intent.account_id, intent.symbol)
        if intent.symbol in self.limits.restricted_symbols or intent.symbol in account.restricted_symbols: return RiskDecision(False, "restricted_symbol", "Symbol is restricted for this account or globally.", intent.account_id, intent.symbol)
        if intent.quantity > self.limits.max_order_quantity: return RiskDecision(False, "max_order_quantity", "Order quantity exceeds limit.", intent.account_id, intent.symbol)
        if account.daily_pnl <= -abs(self.limits.max_daily_loss): return RiskDecision(False, "max_daily_loss", "Account daily loss limit has been breached.", intent.account_id, intent.symbol)
        if account.drawdown >= self.limits.max_drawdown: return RiskDecision(False, "max_drawdown", "Account drawdown limit has been breached.", intent.account_id, intent.symbol)
        signed = intent.quantity if intent.side == OrderSide.BUY else -intent.quantity; projected = account.position_for(intent.symbol) + signed
        if abs(projected) > self.limits.max_position_size: return RiskDecision(False, "max_position_size", "Projected account position breaches limit.", intent.account_id, intent.symbol)
        symbol_exposure = sum(a.position_for(intent.symbol) for a in accounts) + signed
        if abs(symbol_exposure) > self.limits.max_symbol_exposure: return RiskDecision(False, "max_symbol_exposure", "Aggregate symbol exposure breaches limit.", intent.account_id, intent.symbol)
        gross = sum(abs(qty * float(intent.limit_price or 0.0)) for a in accounts for qty in a.positions.values()) + abs(intent.quantity * float(intent.limit_price or 0.0))
        if gross > self.limits.max_gross_exposure: return RiskDecision(False, "max_gross_exposure", "Aggregate gross exposure breaches limit.", intent.account_id, intent.symbol)
        return RiskDecision(True, "allowed", "Order passed pre-trade risk checks.", intent.account_id, intent.symbol, {"projected_account_position": projected, "projected_symbol_exposure": symbol_exposure, "gross_notional": gross})
