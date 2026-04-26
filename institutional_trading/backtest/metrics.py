from __future__ import annotations
from math import sqrt
def compute_sharpe(returns: list[float]) -> float:
    if not returns: return 0.0
    avg = sum(returns)/len(returns); var = sum((x-avg)**2 for x in returns)/len(returns); std = sqrt(var); return 0.0 if std <= 0 else avg/std
def compute_max_drawdown(equity_curve: list[float]) -> float:
    peak = float('-inf'); dd = 0.0
    for x in equity_curve: peak = max(peak, x); dd = min(dd, x-peak)
    return abs(dd)
def compute_win_rate(trade_pnls: list[float]) -> float: return 0.0 if not trade_pnls else sum(1 for p in trade_pnls if p > 0)/len(trade_pnls)
