from __future__ import annotations

from math import sqrt
from typing import Iterable


def compute_equity_curve(pnl_path: Iterable[float]) -> list[float]:
    total = 0.0
    curve: list[float] = []
    for value in pnl_path:
        total += float(value)
        curve.append(total)
    return curve


def compute_max_drawdown(curve: list[float]) -> float:
    peak = float("-inf")
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value - peak)
    return abs(max_drawdown)


def compute_sharpe(returns: list[float]) -> float:
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    std = sqrt(variance)
    if std <= 0:
        return 0.0
    return mean / std
