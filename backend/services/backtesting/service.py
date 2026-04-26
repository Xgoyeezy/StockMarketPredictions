from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend.services.feature_store.service import load_market_state
from backend.services.portfolio_allocator.service import build_risk_state
from backend.services.strategy_engine.registry import build_strategy_desk
from backend.services.strategy_engine.types import BacktestRunRecord


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _subset_market_state(market_state: dict[str, Any], end_index: int) -> dict[str, Any]:
    bars = {}
    for symbol, frame in (market_state.get("bars") or {}).items():
        bars[symbol] = frame.iloc[:end_index].copy()
    return {
        "as_of": market_state.get("as_of") or _utc_now_iso(),
        "requirements": market_state.get("requirements") or [],
        "provider_name": market_state.get("provider_name") or "unknown",
        "bars": bars,
    }


def run_backtest(
    *,
    desk_key: str,
    desk_config: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
) -> BacktestRunRecord:
    request_state = dict(request_payload or {})
    horizon_days = int(request_state.get("horizon_days") or 5)
    warmup_bars = int(request_state.get("warmup_bars") or 60)
    fee_bps = float(request_state.get("fee_bps") or 2.0)
    slippage_bps = float(request_state.get("slippage_bps") or 5.0)
    capital_base = float(desk_config.get("capital_base") or 100000.0)

    desk = build_strategy_desk(desk_key, config=desk_config)
    market_state = load_market_state(desk.get_data_requirements())
    bars = dict(market_state.get("bars") or {})
    history_length = min((len(frame.index) for frame in bars.values() if not frame.empty), default=0)
    if history_length <= warmup_bars + horizon_days:
        return BacktestRunRecord(
            desk_key=desk_key,
            status="insufficient_history",
            started_at=_utc_now_iso(),
            completed_at=_utc_now_iso(),
            summary={"trade_count": 0, "detail": "Not enough history to run walk-forward backtest."},
            artifacts={"equity_curve": [], "trades": []},
        )

    equity = capital_base
    peak_equity = capital_base
    max_drawdown_pct = 0.0
    equity_curve: list[dict[str, Any]] = [{"step": warmup_bars, "equity": round(equity, 2)}]
    trades: list[dict[str, Any]] = []

    risk_state = build_risk_state({"capital_base": capital_base})

    for end_index in range(warmup_bars, history_length - horizon_days):
        snapshot = _subset_market_state(market_state, end_index)
        run = desk.run(market_state=snapshot, risk_state=risk_state)
        if not run.validation.allowed or not run.targets:
            continue
        step_pnl = 0.0
        for target in run.targets:
            symbol_frame = bars.get(target.symbol)
            if symbol_frame is None or symbol_frame.empty or len(symbol_frame.index) <= end_index + horizon_days:
                continue
            entry = float(pd.to_numeric(symbol_frame["Close"], errors="coerce").iloc[end_index - 1])
            exit_price = float(pd.to_numeric(symbol_frame["Close"], errors="coerce").iloc[end_index - 1 + horizon_days])
            if entry <= 0:
                continue
            realized_return = (exit_price / entry) - 1.0
            signed_return = realized_return if target.target_weight >= 0 else -realized_return
            gross_pnl = capital_base * abs(target.target_weight) * signed_return
            trading_cost = capital_base * abs(target.target_weight) * ((fee_bps + slippage_bps) / 10000.0)
            pnl = gross_pnl - trading_cost
            step_pnl += pnl
            trades.append(
                {
                    "desk_key": desk_key,
                    "symbol": target.symbol,
                    "direction": target.direction,
                    "entry_index": end_index,
                    "exit_index": end_index + horizon_days,
                    "gross_pnl": round(gross_pnl, 2),
                    "net_pnl": round(pnl, 2),
                    "signed_return": round(signed_return, 6),
                    "weight": target.target_weight,
                }
            )
        if step_pnl == 0.0:
            continue
        equity += step_pnl
        peak_equity = max(peak_equity, equity)
        drawdown_pct = 0.0 if peak_equity <= 0 else max(0.0, ((peak_equity - equity) / peak_equity) * 100.0)
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
        equity_curve.append({"step": end_index, "equity": round(equity, 2)})

    win_count = sum(1 for trade in trades if float(trade.get("net_pnl") or 0.0) > 0)
    pnl_values = pd.Series([float(trade.get("net_pnl") or 0.0) for trade in trades], dtype=float)
    returns = pd.Series([float(trade.get("signed_return") or 0.0) for trade in trades], dtype=float)
    sharpe = 0.0
    if not returns.empty and float(returns.std(ddof=0)) > 0:
        sharpe = float((returns.mean() / returns.std(ddof=0)) * (252 ** 0.5))

    summary = {
        "trade_count": len(trades),
        "win_rate": round(win_count / len(trades), 4) if trades else 0.0,
        "net_pnl": round(float(pnl_values.sum()) if not pnl_values.empty else 0.0, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "ending_equity": round(equity, 2),
        "turnover": round(sum(abs(float(trade.get("weight") or 0.0)) for trade in trades), 4),
        "sharpe_ratio": round(sharpe, 4),
    }
    return BacktestRunRecord(
        desk_key=desk_key,
        status="completed",
        started_at=_utc_now_iso(),
        completed_at=_utc_now_iso(),
        summary=summary,
        artifacts={"equity_curve": equity_curve, "trades": trades[:250]},
    )
