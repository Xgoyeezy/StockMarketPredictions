from __future__ import annotations
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
class Session(str, Enum):
    PRE_MARKET = "pre_market"; REGULAR = "regular"; AFTER_HOURS = "after_hours"; CLOSED = "closed"
class DataGranularity(str, Enum):
    MINUTE = "minute"; TICK = "tick"
class OrderSide(str, Enum):
    BUY = "buy"; SELL = "sell"
class OrderType(str, Enum):
    LIMIT = "limit"; MARKET = "market"
class OrderState(str, Enum):
    CREATED = "created"; RISK_REJECTED = "risk_rejected"; PENDING_SUBMIT = "pending_submit"; SUBMITTED = "submitted"; ACKNOWLEDGED = "acknowledged"; PARTIALLY_FILLED = "partially_filled"; FILLED = "filled"; CANCEL_REQUESTED = "cancel_requested"; CANCELED = "canceled"; REJECTED = "rejected"; RETRY_PENDING = "retry_pending"; FAILED = "failed"
class HealthStatus(str, Enum):
    HEALTHY = "healthy"; DEGRADED = "degraded"; FAILED = "failed"
def utc_now() -> datetime:
    return datetime.now(tz=UTC)
def serialize_record(value: Any) -> Any:
    if isinstance(value, datetime): return value.astimezone(UTC).isoformat()
    if isinstance(value, Enum): return value.value
    if isinstance(value, dict): return {str(k): serialize_record(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)): return [serialize_record(v) for v in value]
    if is_dataclass(value): return serialize_record(asdict(value))
    if hasattr(value, "to_record"): return value.to_record()
    return value
@dataclass(frozen=True)
class MarketRecord:
    symbol: str; timestamp: datetime; source: str; granularity: DataGranularity; session: Session
    open: float | None = None; high: float | None = None; low: float | None = None; close: float | None = None; last: float | None = None; bid: float | None = None; ask: float | None = None
    volume: int = 0; trade_size: int = 0; sequence: int = 0; metadata: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper()); object.__setattr__(self, "source", self.source.strip().lower())
        if self.timestamp.tzinfo is None: object.__setattr__(self, "timestamp", self.timestamp.replace(tzinfo=UTC))
        if self.price <= 0: raise ValueError("MarketRecord requires a positive derived price.")
    @property
    def price(self) -> float:
        for value in (self.last, self.close, self.bid, self.ask, self.open):
            if value is not None and float(value) > 0: return float(value)
        return 0.0
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str; equity: float; cash: float; positions: dict[str, int] = field(default_factory=dict); realized_pnl: float = 0.0; unrealized_pnl: float = 0.0; daily_pnl: float = 0.0; peak_equity: float | None = None; restricted_symbols: frozenset[str] = field(default_factory=frozenset)
    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", {str(k).upper(): int(v) for k, v in self.positions.items()}); object.__setattr__(self, "restricted_symbols", frozenset(s.upper() for s in self.restricted_symbols))
    def position_for(self, symbol: str) -> int: return int(self.positions.get(symbol.upper(), 0))
    @property
    def drawdown(self) -> float:
        peak = float(self.peak_equity if self.peak_equity is not None else self.equity); return max(peak - float(self.equity), 0.0)
@dataclass(frozen=True)
class PortfolioSnapshot:
    accounts: tuple[AccountSnapshot, ...]; timestamp: datetime = field(default_factory=utc_now)
    def aggregate_position(self, symbol: str) -> int: return sum(account.position_for(symbol) for account in self.accounts)
    @property
    def total_equity(self) -> float: return sum(float(account.equity) for account in self.accounts)
@dataclass(frozen=True)
class Signal:
    signal_id: str; strategy_name: str; strategy_version: str; symbol: str; side: OrderSide; target_quantity: int; limit_price: float; confidence: float; generated_at: datetime; decision_context: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.target_quantity <= 0: raise ValueError("Signal target_quantity must be positive.")
        if self.limit_price <= 0: raise ValueError("Signal limit_price must be positive.")
        if not 0 <= self.confidence <= 1: raise ValueError("Signal confidence must be between 0 and 1.")
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class AllocationEntry:
    account_id: str; quantity: int; weight: float; reason: str = "allocated"
@dataclass(frozen=True)
class AllocationPlan:
    signal_id: str; symbol: str; side: OrderSide; total_quantity: int; entries: tuple[AllocationEntry, ...]; mode: str
    def quantity_for(self, account_id: str) -> int: return sum(entry.quantity for entry in self.entries if entry.account_id == account_id)
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class OrderIntent:
    idempotency_key: str; account_id: str; symbol: str; side: OrderSide; quantity: int; order_type: OrderType; limit_price: float | None; session: Session; extended_hours: bool; strategy_name: str; strategy_version: str; signal_id: str; created_at: datetime; decision_context: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if self.quantity <= 0: raise ValueError("OrderIntent quantity must be positive.")
        if self.order_type == OrderType.LIMIT and (self.limit_price is None or self.limit_price <= 0): raise ValueError("Limit orders require a positive limit_price.")
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class RiskDecision:
    allowed: bool; reason: str; detail: str; account_id: str | None = None; symbol: str | None = None; metrics: dict[str, Any] = field(default_factory=dict)
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class FillReport:
    broker_order_id: str; account_id: str; symbol: str; side: OrderSide; quantity: int; price: float; filled_at: datetime; liquidity_flag: str = "unknown"
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class AuditEvent:
    event_type: str; actor: str; payload: dict[str, Any]; timestamp: datetime = field(default_factory=utc_now); account_id: str | None = None; symbol: str | None = None; order_id: str | None = None
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
@dataclass(frozen=True)
class ReplayEvent:
    sequence: int; event_hash: str; event: AuditEvent
@dataclass(frozen=True)
class ServiceHealth:
    status: HealthStatus; component: str; detail: str; checked_at: datetime = field(default_factory=utc_now); metrics: dict[str, Any] = field(default_factory=dict)
    @property
    def healthy(self) -> bool: return self.status == HealthStatus.HEALTHY
    def to_record(self) -> dict[str, Any]: return serialize_record(self)
