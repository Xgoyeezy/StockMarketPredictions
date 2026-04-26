from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DeskDataRequirement:
    family: str
    tickers: tuple[str, ...]
    period: str
    interval: str
    prepost: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tickers"] = list(self.tickers)
        return payload


@dataclass(frozen=True)
class DeskFeatureFrame:
    desk_key: str
    as_of: str
    feature_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "desk_key": self.desk_key,
            "as_of": self.as_of,
            "feature_rows": [dict(row) for row in self.feature_rows],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class DeskSignal:
    desk_key: str
    generated_at: str
    signal_type: str
    summary: str
    confidence_score: float
    expected_holding_period: str
    risk_estimate: float
    required_capital: float
    components: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeskTargetProposal:
    desk_key: str
    symbol: str
    direction: str
    target_weight: float
    target_notional: float
    confidence_score: float
    expected_holding_period: str
    risk_estimate: float
    required_capital: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeskValidationResult:
    desk_key: str
    allowed: bool
    reason: str
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioTarget:
    symbol: str
    target_weight: float
    target_notional: float
    directions: tuple[str, ...]
    desk_contributions: tuple[dict[str, Any], ...]
    risk_flags: tuple[str, ...] = ()
    order_plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "target_weight": self.target_weight,
            "target_notional": self.target_notional,
            "directions": list(self.directions),
            "desk_contributions": [dict(item) for item in self.desk_contributions],
            "risk_flags": list(self.risk_flags),
            "order_plan": dict(self.order_plan),
        }


@dataclass(frozen=True)
class StrategyRunRecord:
    desk_key: str
    status: str
    market_state: dict[str, Any]
    features: DeskFeatureFrame
    signal: DeskSignal
    targets: tuple[DeskTargetProposal, ...]
    validation: DeskValidationResult
    metrics: dict[str, Any]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "desk_key": self.desk_key,
            "status": self.status,
            "market_state": dict(self.market_state),
            "features": self.features.to_dict(),
            "signal": self.signal.to_dict(),
            "targets": [target.to_dict() for target in self.targets],
            "validation": self.validation.to_dict(),
            "metrics": dict(self.metrics),
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True)
class BacktestRunRecord:
    desk_key: str
    status: str
    started_at: str
    completed_at: str
    summary: dict[str, Any]
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
