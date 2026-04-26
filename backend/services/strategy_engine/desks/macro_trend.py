from __future__ import annotations

from typing import Any

import pandas as pd

from backend.services.feature_store.service import compute_average_volume, compute_realized_vol, compute_return, latest_close
from backend.services.strategy_engine.base import StrategyDesk
from backend.services.strategy_engine.types import (
    DeskDataRequirement,
    DeskFeatureFrame,
    DeskSignal,
    DeskTargetProposal,
    DeskValidationResult,
    utc_now_iso,
)

_DEFAULT_UNIVERSE = ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "TLT", "GLD")


class MacroTrendDesk(StrategyDesk):
    desk_key = "macro_trend"
    display_name = "Macro Trend Desk"
    lifecycle_stage = "paper"
    trading_mode = "paper"
    paper_trading_enabled = True

    def get_data_requirements(self) -> tuple[DeskDataRequirement, ...]:
        universe = tuple(self.config.get("universe") or _DEFAULT_UNIVERSE)
        interval = str(self.config.get("interval") or "1d").strip().lower() or "1d"
        period = str(self.config.get("period") or "2y").strip().lower() or "2y"
        return (
            DeskDataRequirement(
                family="bars",
                tickers=tuple(str(symbol).strip().upper() for symbol in universe if str(symbol).strip()),
                period=period,
                interval=interval,
                prepost=self.include_extended_hours(),
                notes="Macro trend desk trend and volatility inputs with pre/post-market bars enabled.",
            ),
        )

    def compute_features(self, market_state: dict[str, Any]) -> DeskFeatureFrame:
        bars = dict(market_state.get("bars") or {})
        rows: list[dict[str, Any]] = []
        for symbol, frame in bars.items():
            if frame.empty:
                continue
            close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
            if len(close) < 60:
                continue
            ma_50 = float(close.tail(50).mean())
            ma_200 = float(close.tail(min(200, len(close))).mean())
            close_value = latest_close(frame) or 0.0
            momentum_1m = compute_return(frame, 21)
            momentum_3m = compute_return(frame, 63)
            volatility = compute_realized_vol(frame, 20)
            avg_volume = compute_average_volume(frame, 20)
            trend_score = (
                (0.35 if close_value > ma_50 else -0.25)
                + (0.25 if ma_50 > ma_200 else -0.20)
                + max(min(momentum_1m * 2.5, 0.25), -0.25)
                + max(min(momentum_3m * 1.75, 0.25), -0.25)
                - min(volatility * 0.20, 0.20)
            )
            rows.append(
                {
                    "symbol": symbol,
                    "close": close_value,
                    "momentum_1m": momentum_1m,
                    "momentum_3m": momentum_3m,
                    "volatility": volatility,
                    "average_volume": avg_volume,
                    "ma_50": ma_50,
                    "ma_200": ma_200,
                    "trend_score": trend_score,
                    "regime_label": "risk_on" if momentum_3m >= 0 else "risk_off",
                }
            )

        rows.sort(key=lambda item: item["trend_score"], reverse=True)
        regime = "risk_on" if sum(1 for row in rows[:3] if row["trend_score"] > 0) >= 2 else "mixed"
        return DeskFeatureFrame(
            desk_key=self.desk_key,
            as_of=market_state.get("as_of") or utc_now_iso(),
            feature_rows=tuple(rows),
            summary={
                "universe_size": len(rows),
                "regime_label": regime,
                "top_symbols": [row["symbol"] for row in rows[:3]],
            },
        )

    def generate_signal(self, features: DeskFeatureFrame) -> DeskSignal:
        rows = list(features.feature_rows)
        positive_rows = [row for row in rows if float(row.get("trend_score") or 0.0) > 0.05]
        top_rows = positive_rows[: int(self.config.get("max_positions") or 3)]
        confidence = 0.0
        if top_rows:
            confidence = max(0.0, min(1.0, 0.50 + (sum(float(row["trend_score"]) for row in top_rows) / len(top_rows))))
        risk_estimate = sum(float(row.get("volatility") or 0.0) for row in top_rows) / max(len(top_rows), 1)
        return DeskSignal(
            desk_key=self.desk_key,
            generated_at=utc_now_iso(),
            signal_type="trend_following",
            summary="Macro trend signal built from ETF proxy trend and volatility posture.",
            confidence_score=round(confidence, 4),
            expected_holding_period="20d",
            risk_estimate=round(risk_estimate, 4),
            required_capital=float(self.config.get("capital_base") or 100000.0) * min(len(top_rows), 3) / 3.0,
            components={
                "selected_symbols": [row["symbol"] for row in top_rows],
                "regime_label": features.summary.get("regime_label"),
                "candidate_count": len(top_rows),
            },
            metadata={"feature_as_of": features.as_of},
        )

    def generate_target_positions(self, signal: DeskSignal) -> tuple[DeskTargetProposal, ...]:
        selected = list(signal.components.get("selected_symbols") or [])
        if not selected:
            return ()
        capital_base = float(self.config.get("capital_base") or 100000.0)
        raw_weight = 1.0 / len(selected)
        targets: list[DeskTargetProposal] = []
        for symbol in selected:
            targets.append(
                DeskTargetProposal(
                    desk_key=self.desk_key,
                    symbol=symbol,
                    direction="long",
                    target_weight=round(raw_weight, 6),
                    target_notional=round(capital_base * raw_weight, 2),
                    confidence_score=signal.confidence_score,
                    expected_holding_period=signal.expected_holding_period,
                    risk_estimate=signal.risk_estimate,
                    required_capital=round(capital_base * raw_weight, 2),
                    metadata={"signal_type": signal.signal_type, "regime_label": signal.components.get("regime_label")},
                )
            )
        return tuple(targets)

    def validate_signal(self, signal: DeskSignal, risk_state: dict[str, Any]) -> DeskValidationResult:
        max_single_position_pct = float(risk_state.get("max_single_position_pct") or 20.0)
        too_concentrated = len(signal.components.get("selected_symbols") or []) == 1 and max_single_position_pct < 20.0
        allowed = bool(signal.confidence_score >= 0.52 and not too_concentrated and signal.components.get("candidate_count", 0) > 0)
        detail = "Macro trend candidates satisfy trend, volatility, and concentration gates." if allowed else "Macro trend candidates failed confidence or concentration gates."
        return DeskValidationResult(
            desk_key=self.desk_key,
            allowed=allowed,
            reason="accepted" if allowed else "insufficient_edge",
            detail=detail,
            metrics={
                "confidence_score": signal.confidence_score,
                "candidate_count": signal.components.get("candidate_count", 0),
                "max_single_position_pct": max_single_position_pct,
            },
        )
