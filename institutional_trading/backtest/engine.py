from __future__ import annotations
from dataclasses import dataclass, field
from institutional_trading.accounts.allocation import AllocationEngine
from institutional_trading.accounts.models import AccountConfig
from institutional_trading.backtest.metrics import compute_max_drawdown, compute_sharpe, compute_win_rate
from institutional_trading.execution.paper import PaperBrokerAdapter
from institutional_trading.models import AccountSnapshot, AuditEvent, MarketRecord, OrderIntent, OrderSide, OrderType, PortfolioSnapshot, Session
from institutional_trading.risk.engine import RiskEngine
from institutional_trading.strategies.base import StatelessStrategy
@dataclass(frozen=True)
class BacktestResult:
    metrics: dict[str, float]; per_account_pnl: dict[str, float]; audit_events: tuple[AuditEvent, ...]
@dataclass
class BacktestEngine:
    allocation_engine: AllocationEngine; risk_engine: RiskEngine; broker: PaperBrokerAdapter = field(default_factory=PaperBrokerAdapter); latency_ms: float = 5.0; slippage_bps: float = 2.0; partial_fill_ratio: float = 1.0
    def run(self, *, records: list[MarketRecord], strategy: StatelessStrategy, accounts: list[AccountConfig]) -> BacktestResult:
        self.broker.connect(); ordered = sorted(records, key=lambda r: (r.timestamp, r.sequence)); snapshots = [AccountSnapshot(a.account_id, 100000.0, 100000.0, restricted_symbols=a.restricted_symbols) for a in accounts]; cash = {a.account_id: 100000.0 for a in accounts}; positions = {}; events = []; trade_pnls = []; curve = []
        for idx, rec in enumerate(ordered):
            portfolio = PortfolioSnapshot(tuple(snapshots), timestamp=rec.timestamp)
            for sig in strategy.generate_signals(market_records=ordered[:idx+1], portfolio=portfolio):
                plan = self.allocation_engine.allocate(sig, accounts); events.append(AuditEvent("signal_generated", "backtest", sig.to_record(), symbol=sig.symbol))
                for entry in plan.entries:
                    intent = OrderIntent(f"bt:{sig.signal_id}:{entry.account_id}", entry.account_id, sig.symbol, sig.side, entry.quantity, OrderType.LIMIT, sig.limit_price, rec.session, rec.session in {Session.PRE_MARKET, Session.AFTER_HOURS}, strategy.name, strategy.version, sig.signal_id, rec.timestamp, {"latency_ms": self.latency_ms, "slippage_bps": self.slippage_bps})
                    decision = self.risk_engine.validate_order(intent, snapshots); events.append(AuditEvent("risk_decision", "backtest", decision.to_record(), account_id=entry.account_id, symbol=sig.symbol))
                    if not decision.allowed: continue
                    order = self.broker.submit_order(intent); qty = max(1, int(entry.quantity * self.partial_fill_ratio)); price = self._slipped_price(sig.side, sig.limit_price); fill = self.broker.fill_order(order.broker_order_id, quantity=qty, price=price, liquidity_flag="backtest")
                    signed = fill.quantity if sig.side == OrderSide.BUY else -fill.quantity; key = (entry.account_id, sig.symbol); prior = positions.get(key, 0); positions[key] = prior + signed; delta = -fill.quantity * fill.price if sig.side == OrderSide.BUY else fill.quantity * fill.price; cash[entry.account_id] += delta
                    if prior and (prior > 0) != (positions[key] > 0): trade_pnls.append(delta)
                    events.append(AuditEvent("fill", "backtest", fill.to_record(), account_id=entry.account_id, symbol=sig.symbol, order_id=order.broker_order_id))
            curve.append(sum(cash.values()) + sum(q * rec.price for (_, sym), q in positions.items() if sym == rec.symbol))
        returns = [curve[i]-curve[i-1] for i in range(1, len(curve))]; order_ids = {e.order_id for e in events if e.order_id}; fills = [e for e in events if e.event_type == "fill"]
        return BacktestResult({"sharpe": compute_sharpe(returns), "max_drawdown": compute_max_drawdown(curve), "win_rate": compute_win_rate(trade_pnls), "fill_rate": len(fills)/max(len(order_ids),1), "orders": float(len(order_ids)), "fills": float(len(fills))}, {aid: val-100000.0 for aid,val in cash.items()}, tuple(events))
    def _slipped_price(self, side: OrderSide, price: float) -> float:
        bps = self.slippage_bps/10000.0; return float(price) * (1.0 - bps if side == OrderSide.SELL else 1.0 + bps)
