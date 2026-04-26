from __future__ import annotations

from typing import Any

from backend.services.feature_store.service import compute_average_volume, compute_realized_vol, compute_return
from backend.services.strategy_engine.base import StrategyDesk
from backend.services.strategy_engine.types import (
    DeskDataRequirement,
    DeskFeatureFrame,
    DeskSignal,
    DeskTargetProposal,
    DeskValidationResult,
    utc_now_iso,
)

_DEFAULT_UNIVERSE = (
    "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA", "AVGO", "NFLX", "COST", "JPM", "UNH", "XOM",
)


class EquitiesMomentumDesk(StrategyDesk):
    desk_key = "equities_momentum"
    display_name = "Equities Momentum Desk"
    lifecycle_stage = "research"
    trading_mode = "research"
    paper_trading_enabled = False

    def get_data_requirements(self) -> tuple[DeskDataRequirement, ...]:
        return (
            DeskDataRequirement(
                family="bars",
                tickers=tuple(self.config.get("universe") or _DEFAULT_UNIVERSE),
                period=str(self.config.get("period") or "2y"),
                interval=str(self.config.get("interval") or "1d"),
                prepost=self.include_extended_hours(),
                notes="Cross-sectional equities momentum desk history with pre/post-market bars enabled.",
            ),
        )

    def compute_features(self, market_state: dict[str, Any]) -> DeskFeatureFrame:
        rows: list[dict[str, Any]] = []
        for symbol, frame in (market_state.get("bars") or {}).items():
            if frame.empty:
                continue
            score = (
                compute_return(frame, 21) * 0.35
                + compute_return(frame, 63) * 0.40
                + compute_return(frame, 126) * 0.25
                - min(compute_realized_vol(frame, 20) * 0.15, 0.20)
            )
            rows.append(
                {
                    "symbol": symbol,
                    "score": score,
                    "return_1m": compute_return(frame, 21),
                    "return_3m": compute_return(frame, 63),
                    "return_6m": compute_return(frame, 126),
                    "volatility": compute_realized_vol(frame, 20),
                    "average_volume": compute_average_volume(frame, 20),
                }
            )
        rows.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return DeskFeatureFrame(
            desk_key=self.desk_key,
            as_of=market_state.get("as_of") or utc_now_iso(),
            feature_rows=tuple(rows),
            summary={"leaderboard": [row["symbol"] for row in rows[:5]], "universe_size": len(rows)},
        )

    def generate_signal(self, features: DeskFeatureFrame) -> DeskSignal:
        leaders = list(features.summary.get("leaderboard") or [])
        top_rows = list(features.feature_rows[: min(5, len(features.feature_rows))])
        confidence = min(0.95, 0.5 + sum(float(row.get("score") or 0.0) for row in top_rows) / max(len(top_rows), 1))
        risk_estimate = sum(float(row.get("volatility") or 0.0) for row in top_rows) / max(len(top_rows), 1)
        return DeskSignal(
            desk_key=self.desk_key,
            generated_at=utc_now_iso(),
            signal_type="cross_sectional_rank",
            summary="Cross-sectional momentum ranking model for liquid equities.",
            confidence_score=max(0.0, round(confidence, 4)),
            expected_holding_period="15d",
            risk_estimate=round(risk_estimate, 4),
            required_capital=float(self.config.get("capital_base") or 100000.0),
            components={"leaders": leaders, "candidate_count": len(leaders)},
            metadata={"feature_as_of": features.as_of},
        )

    def generate_target_positions(self, signal: DeskSignal) -> tuple[DeskTargetProposal, ...]:
        leaders = list(signal.components.get("leaders") or [])
        if not leaders:
            return ()
        weight = 1.0 / len(leaders)
        capital_base = float(self.config.get("capital_base") or 100000.0)
        return tuple(
            DeskTargetProposal(
                desk_key=self.desk_key,
                symbol=symbol,
                direction="long",
                target_weight=round(weight, 6),
                target_notional=round(capital_base * weight, 2),
                confidence_score=signal.confidence_score,
                expected_holding_period=signal.expected_holding_period,
                risk_estimate=signal.risk_estimate,
                required_capital=round(capital_base * weight, 2),
                metadata={"research_only": True},
            )
            for symbol in leaders
        )

    def validate_signal(self, signal: DeskSignal, risk_state: dict[str, Any]) -> DeskValidationResult:
        return DeskValidationResult(
            desk_key=self.desk_key,
            allowed=False,
            reason="research_only",
            detail="Equities momentum desk is available for research and backtesting until paper validation is promoted.",
            metrics={"candidate_count": signal.components.get("candidate_count", 0), "confidence_score": signal.confidence_score},
        )
