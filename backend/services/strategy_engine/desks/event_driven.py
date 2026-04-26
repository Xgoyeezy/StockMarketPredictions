from __future__ import annotations

from typing import Any

from backend.services.feature_store.service import compute_gap, compute_volume_spike
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

_DEFAULT_UNIVERSE = ("AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA")


class EventDrivenDesk(StrategyDesk):
    desk_key = "event_driven"
    display_name = "Event-Driven Desk"
    lifecycle_stage = "research"
    trading_mode = "research"
    paper_trading_enabled = False

    def get_data_requirements(self) -> tuple[DeskDataRequirement, ...]:
        return (
            DeskDataRequirement(
                family="bars",
                tickers=tuple(self.config.get("universe") or _DEFAULT_UNIVERSE),
                period=str(self.config.get("period") or "6mo"),
                interval=str(self.config.get("interval") or "1d"),
                prepost=self.include_extended_hours(),
                notes="Event-driven desk earnings and catalyst context with pre/post-market bars enabled.",
            ),
        )

    def compute_features(self, market_state: dict[str, Any]) -> DeskFeatureFrame:
        provider = get_market_data_provider()
        rows: list[dict[str, Any]] = []
        for symbol, frame in (market_state.get("bars") or {}).items():
            if frame.empty:
                continue
            earnings = provider.get_earnings_events(symbol, limit=3)
            next_earnings = earnings[0].event_date.isoformat() if earnings else None
            rows.append(
                {
                    "symbol": symbol,
                    "next_earnings_at": next_earnings,
                    "gap_pct": compute_gap(frame),
                    "volume_spike": compute_volume_spike(frame),
                    "event_score": min(1.0, max(0.0, abs(compute_gap(frame)) * 6.0 + max(compute_volume_spike(frame) - 1.0, 0.0) * 0.25)),
                }
            )
        rows.sort(key=lambda item: float(item.get("event_score") or 0.0), reverse=True)
        return DeskFeatureFrame(
            desk_key=self.desk_key,
            as_of=market_state.get("as_of") or utc_now_iso(),
            feature_rows=tuple(rows),
            summary={"tracked_symbols": len(rows), "active_events": [row["symbol"] for row in rows[:3]]},
        )

    def generate_signal(self, features: DeskFeatureFrame) -> DeskSignal:
        active = list(features.summary.get("active_events") or [])
        top_score = float(features.feature_rows[0].get("event_score") or 0.0) if features.feature_rows else 0.0
        return DeskSignal(
            desk_key=self.desk_key,
            generated_at=utc_now_iso(),
            signal_type="event_window",
            summary="Event-driven catalyst posture from earnings timing, gaps, and volume spikes.",
            confidence_score=round(min(0.95, 0.45 + top_score / 2.0), 4) if active else 0.0,
            expected_holding_period="3d",
            risk_estimate=round(top_score, 4),
            required_capital=float(self.config.get("capital_base") or 100000.0),
            components={"active_events": active},
            metadata={"feature_as_of": features.as_of},
        )

    def generate_target_positions(self, signal: DeskSignal) -> tuple[DeskTargetProposal, ...]:
        active = list(signal.components.get("active_events") or [])
        if not active:
            return ()
        weight = 1.0 / len(active)
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
            for symbol in active
        )

    def validate_signal(self, signal: DeskSignal, risk_state: dict[str, Any]) -> DeskValidationResult:
        return DeskValidationResult(
            desk_key=self.desk_key,
            allowed=False,
            reason="research_only",
            detail="Event-driven desk remains research/backtest-only until event and exit handling pass paper validation.",
            metrics={"active_events": len(signal.components.get("active_events") or []), "confidence_score": signal.confidence_score},
        )
