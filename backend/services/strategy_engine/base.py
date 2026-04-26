from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.services.strategy_engine.types import (
    DeskDataRequirement,
    DeskFeatureFrame,
    DeskSignal,
    DeskTargetProposal,
    DeskValidationResult,
    StrategyRunRecord,
    utc_now_iso,
)


class StrategyDesk(ABC):
    desk_key: str = "strategy"
    display_name: str = "Strategy"
    lifecycle_stage: str = "research"
    trading_mode: str = "research"
    paper_trading_enabled: bool = False

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def include_extended_hours(self) -> bool:
        return bool(self.config.get("include_extended_hours", True))

    @abstractmethod
    def get_data_requirements(self) -> tuple[DeskDataRequirement, ...]:
        raise NotImplementedError

    @abstractmethod
    def compute_features(self, market_state: dict[str, Any]) -> DeskFeatureFrame:
        raise NotImplementedError

    @abstractmethod
    def generate_signal(self, features: DeskFeatureFrame) -> DeskSignal:
        raise NotImplementedError

    @abstractmethod
    def generate_target_positions(self, signal: DeskSignal) -> tuple[DeskTargetProposal, ...]:
        raise NotImplementedError

    @abstractmethod
    def validate_signal(self, signal: DeskSignal, risk_state: dict[str, Any]) -> DeskValidationResult:
        raise NotImplementedError

    def publish_targets(self, targets: tuple[DeskTargetProposal, ...]) -> dict[str, Any]:
        required_capital = float(sum(max(target.required_capital, 0.0) for target in targets))
        return {
            "desk_key": self.desk_key,
            "published_at": utc_now_iso(),
            "target_count": len(targets),
            "required_capital": required_capital,
            "targets": [target.to_dict() for target in targets],
        }

    def publish_metrics(
        self,
        *,
        market_state: dict[str, Any],
        features: DeskFeatureFrame,
        signal: DeskSignal,
        targets: tuple[DeskTargetProposal, ...],
        validation: DeskValidationResult,
    ) -> dict[str, Any]:
        return {
            "desk_key": self.desk_key,
            "feature_count": len(features.feature_rows),
            "target_count": len(targets),
            "confidence_score": signal.confidence_score,
            "expected_holding_period": signal.expected_holding_period,
            "risk_estimate": signal.risk_estimate,
            "validation_allowed": validation.allowed,
            "market_state_as_of": market_state.get("as_of"),
            "include_extended_hours": self.include_extended_hours(),
        }

    def run(self, *, market_state: dict[str, Any], risk_state: dict[str, Any]) -> StrategyRunRecord:
        features = self.compute_features(market_state)
        signal = self.generate_signal(features)
        targets = self.generate_target_positions(signal)
        validation = self.validate_signal(signal, risk_state)
        metrics = self.publish_metrics(
            market_state=market_state,
            features=features,
            signal=signal,
            targets=targets,
            validation=validation,
        )
        status = "accepted" if validation.allowed else "blocked"
        return StrategyRunRecord(
            desk_key=self.desk_key,
            status=status,
            market_state=market_state,
            features=features,
            signal=signal,
            targets=targets,
            validation=validation,
            metrics=metrics,
            generated_at=utc_now_iso(),
        )
