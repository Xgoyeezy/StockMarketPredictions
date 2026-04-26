from __future__ import annotations

from typing import Any

from backend.services.feature_store.service import compute_realized_vol
from backend.services.market_data.provider_registry import get_market_data_provider
from backend.services.strategy_engine.base import StrategyDesk
from backend.services.strategy_engine.types import (
    DeskDataRequirement,
    DeskFeatureFrame,
    DeskSignal,
    DeskTargetProposal,
    DeskValidationResult,
    utc_now_iso,
)

_DEFAULT_UNIVERSE = ("SPY", "QQQ", "AAPL", "NVDA", "TSLA")


class OptionsVolatilityDesk(StrategyDesk):
    desk_key = "options_volatility"
    display_name = "Options Volatility Desk"
    lifecycle_stage = "research"
    trading_mode = "research"
    paper_trading_enabled = False

    def get_data_requirements(self) -> tuple[DeskDataRequirement, ...]:
        return (
            DeskDataRequirement(
                family="bars",
                tickers=tuple(self.config.get("universe") or _DEFAULT_UNIVERSE),
                period=str(self.config.get("period") or "1y"),
                interval=str(self.config.get("interval") or "1d"),
                prepost=self.include_extended_hours(),
                notes="Options vol desk realized volatility inputs for IV/RV comparisons with pre/post-market bars enabled.",
            ),
        )

    def compute_features(self, market_state: dict[str, Any]) -> DeskFeatureFrame:
        provider = get_market_data_provider()
        rows: list[dict[str, Any]] = []
        for symbol, frame in (market_state.get("bars") or {}).items():
            if frame.empty:
                continue
            options_flow = provider.get_options_flow_snapshot(symbol)
            realized_vol = compute_realized_vol(frame, 20)
            rows.append(
                {
                    "symbol": symbol,
                    "implied_volatility": float(options_flow.implied_volatility or 0.0),
                    "realized_volatility": realized_vol,
                    "iv_rv_spread": float(options_flow.implied_volatility or 0.0) - realized_vol,
                    "skew_score": float(options_flow.skew_score or 0.0),
                    "term_structure": float(options_flow.iv_rank or 0.0),
                    "source": options_flow.source,
                }
            )
        rows.sort(key=lambda item: abs(float(item.get("iv_rv_spread") or 0.0)), reverse=True)
        return DeskFeatureFrame(
            desk_key=self.desk_key,
            as_of=market_state.get("as_of") or utc_now_iso(),
            feature_rows=tuple(rows),
            summary={"tracked_symbols": len(rows), "leaders": [row["symbol"] for row in rows[:3]]},
        )

    def generate_signal(self, features: DeskFeatureFrame) -> DeskSignal:
        top_row = features.feature_rows[0] if features.feature_rows else {}
        spread = abs(float(top_row.get("iv_rv_spread") or 0.0))
        return DeskSignal(
            desk_key=self.desk_key,
            generated_at=utc_now_iso(),
            signal_type="iv_vs_rv",
            summary="Options volatility posture from implied/realized spread, skew, and term structure.",
            confidence_score=round(min(0.95, 0.40 + spread), 4) if top_row else 0.0,
            expected_holding_period="10d",
            risk_estimate=round(spread, 4),
            required_capital=float(self.config.get("capital_base") or 100000.0),
            components={"leaders": [row["symbol"] for row in features.feature_rows[:3]]},
            metadata={"feature_as_of": features.as_of},
        )

    def generate_target_positions(self, signal: DeskSignal) -> tuple[DeskTargetProposal, ...]:
        leaders = list(signal.components.get("leaders") or [])
        if not leaders:
            return ()
        return tuple(
            DeskTargetProposal(
                desk_key=self.desk_key,
                symbol=symbol,
                direction="vol_relative",
                target_weight=0.0,
                target_notional=0.0,
                confidence_score=signal.confidence_score,
                expected_holding_period=signal.expected_holding_period,
                risk_estimate=signal.risk_estimate,
                required_capital=0.0,
                metadata={"research_only": True, "note": "Options volatility desk is not yet paper-routable."},
            )
            for symbol in leaders
        )

    def validate_signal(self, signal: DeskSignal, risk_state: dict[str, Any]) -> DeskValidationResult:
        return DeskValidationResult(
            desk_key=self.desk_key,
            allowed=False,
            reason="research_only",
            detail="Options volatility desk remains research-only until options routing, Greeks management, and data quality are validated.",
            metrics={"leader_count": len(signal.components.get("leaders") or []), "confidence_score": signal.confidence_score},
        )
