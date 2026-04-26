from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


AllowedInterval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


class AnalyzeRequest(BaseModel):
    ticker: str = Field(..., min_length=1, description="Ticker symbol, e.g. SPY")
    interval: AllowedInterval = "5m"
    horizon: int = Field(5, ge=1, le=50)
    regular_hours_only: bool = False
    include_history: bool = False
    include_live_price: bool = True
    include_contract_lookup: bool = True
    include_event_lookup: bool = True
    include_alignment: bool = True
    use_fast_model: bool = False

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("Ticker cannot be empty.")
        return cleaned


class ScanRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    interval: AllowedInterval = "5m"
    horizon: int = Field(5, ge=1, le=50)
    regular_hours_only: bool = False
    top_n: int = Field(10, ge=1, le=100)
    include_errors: bool = True
    include_contract_lookup: bool = True
    include_event_lookup: bool = True
    include_alignment: bool = True
    use_fast_model: bool = False

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip().upper()
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)
        return normalized


class WatchlistRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    interval: AllowedInterval = "5m"
    horizon: int = Field(5, ge=1, le=50)
    regular_hours_only: bool = False
    limit: int = Field(25, ge=1, le=250)
    sort_by: str = "setup_score"
    descending: bool = True
    include_contract_lookup: bool = True
    include_event_lookup: bool = True
    include_alignment: bool = True
    use_fast_model: bool = False

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip().upper()
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)
        return normalized


class CompareRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list, min_length=2, max_length=12)
    interval: AllowedInterval = "5m"
    horizon: int = Field(5, ge=1, le=50)
    points_limit: int = Field(250, ge=50, le=1000)
    regular_hours_only: bool = False

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip().upper()
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)
        if len(normalized) < 2:
            raise ValueError("At least two unique tickers are required.")
        return normalized[:12]


class LivePricesRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list, min_length=1)

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip().upper()
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)
        if not normalized:
            raise ValueError("At least one ticker is required.")
        return normalized


class OpenTradeRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    interval: AllowedInterval = "5m"
    horizon: int = Field(5, ge=1, le=50)
    account_target_type: Literal["personal", "linked_client"] = "personal"
    linked_account_id: str | None = Field(default=None, max_length=36)
    execution_mode: Literal["manual_approval", "automated_entry", "portfolio_target_execution"] = "manual_approval"
    live_price: float | None = Field(default=None, gt=0)
    account_size: float = Field(10000.0, gt=0)
    risk_percent: float = Field(1.0, gt=0, le=100)
    requested_quantity: float | None = Field(default=None, gt=0)
    instrument_type: Literal["listed_option", "equity"] = "listed_option"
    broker_side: Literal["buy", "sell"] = "buy"
    option_strategy: str | None = Field(default=None, max_length=80)
    option_right: Literal["call", "put"] | None = None
    contract_symbol: str | None = Field(default=None, max_length=80)
    contract_expiration: str | None = Field(default=None, max_length=40)
    contract_strike: float | None = Field(default=None, gt=0)
    contract_bid: float | None = Field(default=None, ge=0)
    contract_ask: float | None = Field(default=None, ge=0)
    contract_mid: float | None = Field(default=None, gt=0)
    contract_spread_pct: float | None = Field(default=None, ge=0)
    contract_volume: int | None = Field(default=None, ge=0)
    contract_open_interest: int | None = Field(default=None, ge=0)
    contract_quote_timestamp: str | None = Field(default=None, max_length=80)
    execution_intent: Literal["default", "desk", "broker_paper", "broker_live"] = "default"
    order_type: Literal["market", "limit", "stop_market", "stop_limit", "trailing_stop"] = "market"
    time_in_force: Literal["day", "day_ext", "gtc_90d"] = "day"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    trailing_percent: float | None = Field(default=None, gt=0)
    extended_hours: bool = False
    capital_preservation_mode: bool = False
    tiny_account_mode: bool = False
    fractional_shares_only: bool = False
    regular_hours_only: bool = False
    max_daily_loss_r: float | None = Field(default=None, gt=0, le=25)
    max_consecutive_losses: int | None = Field(default=None, ge=1, le=25)
    max_open_positions: int | None = Field(default=None, ge=1, le=25)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    equities_only: bool = False
    limit_orders_only: bool = False
    long_only: bool = False
    route_family: str | None = Field(default=None, max_length=40)
    route_version: str | None = Field(default=None, max_length=80)
    automation_entry_reason: str | None = Field(default=None, max_length=80)
    thesis_direction: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=80)
    portfolio_target_run_id: str | None = Field(default=None, max_length=36)
    strategy_desk_key: str | None = Field(default=None, max_length=80)
    desk_contributions: list[dict[str, Any]] | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("Ticker cannot be empty.")
        return cleaned

    @field_validator(
        "option_strategy",
        "contract_symbol",
        "contract_expiration",
        "contract_quote_timestamp",
        "route_family",
        "route_version",
        "automation_entry_reason",
        "thesis_direction",
        "source",
        "portfolio_target_run_id",
        "strategy_desk_key",
        "linked_account_id",
    )
    @classmethod
    def normalize_optional_trade_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class LinkedBrokerageAccountAutomationUpdateRequest(BaseModel):
    client_auto_trading_opt_in: bool | None = None
    operator_auto_trading_enabled: bool | None = None
    automation_paused: bool | None = None
    account_size: float | None = Field(default=None, gt=0)
    risk_percent: float | None = Field(default=None, gt=0, le=100)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    max_open_positions: int | None = Field(default=None, ge=1, le=100)


class LinkedBrokerageAccountStartRequest(BaseModel):
    environment: Literal["paper", "live"] = "paper"
    redirect_path: str | None = Field(default="/settings", max_length=240)
    linked_account_id: str | None = Field(default=None, max_length=36)

    @field_validator("redirect_path", "linked_account_id")
    @classmethod
    def normalize_optional_brokerage_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class TradeIntentDecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=400)
    reason: str | None = Field(default=None, max_length=400)
    conditions: list[str] | None = Field(default=None, max_length=8)

    @field_validator("note", "reason")
    @classmethod
    def normalize_optional_decision_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("conditions")
    @classmethod
    def normalize_decision_conditions(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                normalized.append(cleaned[:240])
                seen.add(key)
        return normalized or None


class TradeDecisionReviewRequest(BaseModel):
    standard_path: str | None = Field(default=None, max_length=800)
    requested_deviation: str | None = Field(default=None, max_length=800)
    thesis_rationale: str | None = Field(default=None, max_length=1000)
    accepted_risk: bool = False
    accepted_risk_owner: str | None = Field(default=None, max_length=160)
    accepted_risk_note: str | None = Field(default=None, max_length=800)
    challenge_raised: bool = False
    challenge_notes: str | None = Field(default=None, max_length=800)
    unresolved_conditions: list[str] | None = Field(default=None, max_length=8)
    mark_decision_ready: bool = False

    @field_validator(
        "standard_path",
        "requested_deviation",
        "thesis_rationale",
        "accepted_risk_owner",
        "accepted_risk_note",
        "challenge_notes",
    )
    @classmethod
    def normalize_optional_review_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("unresolved_conditions")
    @classmethod
    def normalize_review_conditions(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                normalized.append(cleaned[:240])
                seen.add(key)
        return normalized or None


class TradeEvidenceRegisterRequest(BaseModel):
    items: list[dict[str, Any]] | None = Field(default=None, max_length=12)
    notes: str | None = Field(default=None, max_length=800)

    @field_validator("notes")
    @classmethod
    def normalize_evidence_notes(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class TradeScenarioSaveRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    outcome: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=800)
    market_regime: str | None = Field(default=None, max_length=120)
    release_status: str | None = Field(default="current_release", max_length=80)
    setup_label: str | None = Field(default=None, max_length=120)

    @field_validator("name", "outcome", "notes", "market_regime", "release_status", "setup_label")
    @classmethod
    def normalize_optional_scenario_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class ControlChangeRequest(BaseModel):
    control_type: Literal[
        "linked_account_change",
        "withdrawal_or_payment_change",
        "api_key_rotation",
        "risk_limit_change",
        "automation_enablement",
        "live_mode_activation",
        "billing_payment_change",
        "other",
    ] = "other"
    summary: str = Field(..., min_length=3, max_length=240)
    applies_to: str | None = Field(default=None, max_length=160)
    current_value: str | None = Field(default=None, max_length=800)
    requested_value: str | None = Field(default=None, max_length=800)
    rationale: str | None = Field(default=None, max_length=800)

    @field_validator("summary", "applies_to", "current_value", "requested_value", "rationale")
    @classmethod
    def normalize_control_change_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class CloseTradeRequest(BaseModel):
    trade_index: int = Field(..., ge=0)
    close_underlying_price: float = Field(..., gt=0)
    close_contract_mid: float = Field(..., gt=0)
    close_limit_price: float | None = Field(default=None, gt=0)
    close_fraction: float = Field(1.0, gt=0, le=1)


class ReplaceOrderRequest(BaseModel):
    instrument_type: Literal["listed_option", "equity"] | None = None
    option_strategy: str | None = Field(default=None, max_length=80)
    option_right: Literal["call", "put"] | None = None
    contract_symbol: str | None = Field(default=None, max_length=80)
    contract_expiration: str | None = Field(default=None, max_length=40)
    contract_strike: float | None = Field(default=None, gt=0)
    contract_bid: float | None = Field(default=None, ge=0)
    contract_ask: float | None = Field(default=None, ge=0)
    contract_mid: float | None = Field(default=None, gt=0)
    contract_spread_pct: float | None = Field(default=None, ge=0)
    contract_volume: int | None = Field(default=None, ge=0)
    contract_open_interest: int | None = Field(default=None, ge=0)
    contract_quote_timestamp: str | None = Field(default=None, max_length=80)
    order_type: Literal["market", "limit", "stop_market", "stop_limit", "trailing_stop"] = "limit"
    time_in_force: Literal["day", "day_ext", "gtc_90d"] = "day"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    trailing_percent: float | None = Field(default=None, gt=0)
    extended_hours: bool = False

    @field_validator("option_strategy", "contract_symbol", "contract_expiration", "contract_quote_timestamp")
    @classmethod
    def normalize_optional_trade_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class FillOrderRequest(BaseModel):
    live_price: float = Field(..., gt=0)


class HistoryRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    interval: AllowedInterval = "5m"

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("Ticker cannot be empty.")
        return cleaned


class ChartRequest(BaseModel):
    ticker: str = Field(..., min_length=1)
    interval: AllowedInterval = "5m"
    points_limit: int = Field(300, ge=50, le=5000)
    regular_hours_only: bool = False

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("Ticker cannot be empty.")
        return cleaned


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: str


class ApiEnvelope(BaseModel):
    ok: bool = True
    data: Any
    meta: dict[str, Any] = Field(default_factory=dict)


class DeskSummary(BaseModel):
    tenant_slug: str
    tenant_name: str
    paper_account_status: str
    live_account_status: str
    open_trades: int
    pending_orders: int
    alerts: int
    last_activity_at: str | None = None


class AuthLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    name: str = Field(..., min_length=1, max_length=160)
    tenant_slug: str | None = Field(default=None, max_length=80)
    invite_token: str | None = Field(default=None, max_length=160)
    organization_name: str | None = Field(default=None, max_length=160)
    create_organization_if_missing: bool = True

    @field_validator("email")
    @classmethod
    def normalize_auth_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Email cannot be empty.")
        return cleaned

    @field_validator("name", "organization_name")
    @classmethod
    def normalize_auth_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("tenant_slug")
    @classmethod
    def normalize_auth_tenant_slug(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("invite_token")
    @classmethod
    def normalize_auth_invite_token(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class SaveWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    page: str = Field(default='dashboard', min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default='', max_length=400)
    pinned: bool = False
    tags: list[str] = Field(default_factory=list)

    @field_validator('name')
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError('Workspace name cannot be empty.')
        return cleaned

    @field_validator('page')
    @classmethod
    def normalize_page(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError('Workspace page cannot be empty.')
        return cleaned


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    page: str | None = Field(default=None, max_length=32)
    payload: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=400)
    pinned: bool | None = None
    tags: list[str] | None = None


class WorkspaceImportRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = Field(default='merge', pattern='^(merge|replace)$')


class TickerSymbolRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=8)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("Ticker cannot be empty.")
        return cleaned


class NoteCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(default='', max_length=4000)
    ticker: str = Field(default='', max_length=8)
    tags: list[str] = Field(default_factory=list)
    owner: str = Field(default='', max_length=40)
    source_url: str | None = Field(default=None, max_length=300)
    checklist: list[dict[str, Any]] = Field(default_factory=list)
    related_note_ids: list[str] = Field(default_factory=list)
    blocked_by_ids: list[str] = Field(default_factory=list)
    pinned: bool = False
    priority: str = Field(default='medium', pattern='^(low|medium|high)$')
    note_type: str = Field(default='general', pattern='^(general|trade_idea|risk_review|market_note|todo)$')
    due_at: str | None = None
    reminder_at: str | None = None
    recurrence: str = Field(default='none', pattern='^(none|daily|weekly|weekdays|monthly)$')
    recurrence_end_at: str | None = None
    completed: bool = False
    estimate_minutes: int = Field(default=0, ge=0, le=100000)
    spent_minutes: int = Field(default=0, ge=0, le=100000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Note title cannot be empty.")
        return cleaned

    @field_validator("ticker")
    @classmethod
    def normalize_note_ticker(cls, value: str) -> str:
        return value.strip().upper()


class NoteUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=4000)
    ticker: str | None = Field(default=None, max_length=8)
    tags: list[str] | None = None
    owner: str | None = Field(default=None, max_length=40)
    source_url: str | None = Field(default=None, max_length=300)
    checklist: list[dict[str, Any]] | None = None
    related_note_ids: list[str] | None = None
    blocked_by_ids: list[str] | None = None
    pinned: bool | None = None
    archived: bool | None = None
    priority: str | None = Field(default=None, pattern='^(low|medium|high)$')
    note_type: str | None = Field(default=None, pattern='^(general|trade_idea|risk_review|market_note|todo)$')
    due_at: str | None = None
    reminder_at: str | None = None
    recurrence: str | None = Field(default=None, pattern='^(none|daily|weekly|weekdays|monthly)$')
    recurrence_end_at: str | None = None
    completed: bool | None = None
    estimate_minutes: int | None = Field(default=None, ge=0, le=100000)
    spent_minutes: int | None = Field(default=None, ge=0, le=100000)

    @field_validator("ticker")
    @classmethod
    def normalize_note_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value.strip().upper()


class NotesImportRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = Field(default='merge', pattern='^(merge|replace)$')


class NotesBulkActionRequest(BaseModel):
    note_ids: list[str] = Field(default_factory=list, min_length=1, max_length=100)
    action: str = Field(..., pattern='^(complete|reopen|archive|restore|delete|pin|unpin)$')


class NoteSnoozeRequest(BaseModel):
    minutes: int = Field(..., ge=1, le=43200)


class OrganizationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=80)
    plan_key: str = Field(default="starter", min_length=1, max_length=64)
    billing_email: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_org_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Organization name cannot be empty.")
        return cleaned

    @field_validator("slug")
    @classmethod
    def normalize_org_slug(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None


class OrganizationActivateRequest(BaseModel):
    tenant_slug: str = Field(..., min_length=1, max_length=80)

    @field_validator("tenant_slug")
    @classmethod
    def normalize_tenant_slug(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Tenant slug cannot be empty.")
        return cleaned


class OrganizationMemberInviteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field(default="viewer", min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=500)

    @field_validator("email")
    @classmethod
    def normalize_member_invite_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Invite email cannot be empty.")
        return cleaned

    @field_validator("role")
    @classmethod
    def normalize_member_invite_role(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Invite role cannot be empty.")
        return cleaned

    @field_validator("name", "message")
    @classmethod
    def normalize_member_invite_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class OrganizationMemberUpdateRequest(BaseModel):
    membership_id: str = Field(..., min_length=1, max_length=120)
    role: str = Field(..., min_length=1, max_length=32)

    @field_validator("membership_id", "role")
    @classmethod
    def normalize_member_update_fields(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Membership update fields cannot be empty.")
        return cleaned


class OrganizationMemberRemoveRequest(BaseModel):
    membership_id: str = Field(..., min_length=1, max_length=120)

    @field_validator("membership_id")
    @classmethod
    def normalize_member_remove_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Membership id cannot be empty.")
        return cleaned


class OrganizationInvitationActionRequest(BaseModel):
    invitation_id: str = Field(..., min_length=1, max_length=120)
    action: Literal["cancel", "resend"]

    @field_validator("invitation_id", "action")
    @classmethod
    def normalize_invitation_action_fields(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Invitation action fields cannot be empty.")
        return cleaned


_HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_DOMAIN_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


class OrganizationBrandingUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    billing_email: str | None = Field(default=None, max_length=255)
    logo_url: str | None = Field(default=None, max_length=512)
    app_name: str | None = Field(default=None, max_length=120)
    app_tagline: str | None = Field(default=None, max_length=240)
    accent_primary: str | None = Field(default=None, max_length=9)
    accent_secondary: str | None = Field(default=None, max_length=9)
    background_color: str | None = Field(default=None, max_length=9)
    surface_color: str | None = Field(default=None, max_length=9)
    text_color: str | None = Field(default=None, max_length=9)
    support_email: str | None = Field(default=None, max_length=255)
    support_url: str | None = Field(default=None, max_length=500)

    @field_validator("name", "billing_email", "logo_url", "app_name", "app_tagline", "support_email", "support_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator(
        "accent_primary",
        "accent_secondary",
        "background_color",
        "surface_color",
        "text_color",
    )
    @classmethod
    def normalize_optional_hex_color(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if not cleaned:
            return None
        if not _HEX_COLOR_PATTERN.fullmatch(cleaned):
            raise ValueError("Colors must use #RRGGBB or #RRGGBBAA format.")
        return cleaned.upper()


class OrganizationAuthProviderRecordInput(BaseModel):
    provider_id: str | None = Field(default=None, max_length=120)
    provider_key: Literal["auth0", "oidc"]
    label: str = Field(..., min_length=1, max_length=120)
    enabled: bool = True
    email_domains: list[str] = Field(default_factory=list)
    organization_hint: str | None = Field(default=None, max_length=120)
    connection_hint: str | None = Field(default=None, max_length=120)
    auth0_domain: str | None = Field(default=None, max_length=255)
    issuer: str | None = Field(default=None, max_length=500)
    authorize_url: str | None = Field(default=None, max_length=500)
    token_url: str | None = Field(default=None, max_length=500)
    userinfo_url: str | None = Field(default=None, max_length=500)
    logout_url: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=255)
    client_secret: str | None = Field(default=None, max_length=500)
    audience: str | None = Field(default=None, max_length=500)
    scope: str | None = Field(default=None, max_length=255)
    allow_signup: bool | None = None
    is_default: bool = False

    @field_validator("provider_id")
    @classmethod
    def normalize_provider_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("label", "organization_hint", "connection_hint")
    @classmethod
    def normalize_provider_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator(
        "issuer",
        "authorize_url",
        "token_url",
        "userinfo_url",
        "logout_url",
        "client_id",
        "client_secret",
        "audience",
        "scope",
        "auth0_domain",
    )
    @classmethod
    def normalize_provider_config_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("email_domains")
    @classmethod
    def normalize_provider_email_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if not cleaned:
                continue
            if not _DOMAIN_PATTERN.fullmatch(cleaned):
                raise ValueError("Provider email domains must be valid hostnames.")
            if cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized


class OrganizationDeliveryUpdateRequest(BaseModel):
    primary_domain: str | None = Field(default=None, max_length=255)
    secondary_domains: list[str] | None = None
    domain_status: Literal["draft", "pending_verification", "verified", "live"] | None = None
    sender_name: str | None = Field(default=None, max_length=160)
    sender_email: str | None = Field(default=None, max_length=255)
    reply_to_email: str | None = Field(default=None, max_length=255)
    mail_from_subdomain: str | None = Field(default=None, max_length=120)
    email_signature: str | None = Field(default=None, max_length=500)
    email_provider: Literal["none", "resend", "postmark", "sendgrid", "ses", "custom-smtp"] | None = None
    provider_status: Literal["draft", "configured", "ready", "live"] | None = None
    template_set_name: str | None = Field(default=None, max_length=120)
    release_channel: Literal["stable", "pilot", "beta"] | None = None
    auth0_organization: str | None = Field(default=None, max_length=120)
    auth0_connection: str | None = Field(default=None, max_length=120)
    sso_email_domain: str | None = Field(default=None, max_length=255)
    enabled_providers: list[Literal["local-session", "auth0", "oidc"]] | None = None
    auth_policy: Literal["default", "prefer_sso", "require_sso", "local_only"] | None = None
    preferred_provider: Literal["default", "auth0", "oidc", "local-session"] | None = None
    auth_provider_records: list[OrganizationAuthProviderRecordInput] | None = None

    @field_validator("primary_domain")
    @classmethod
    def normalize_optional_primary_domain(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if not _DOMAIN_PATTERN.fullmatch(cleaned):
            raise ValueError("Primary domain must be a valid hostname.")
        return cleaned

    @field_validator("secondary_domains")
    @classmethod
    def normalize_optional_secondary_domains(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if not cleaned:
                continue
            if not _DOMAIN_PATTERN.fullmatch(cleaned):
                raise ValueError("Secondary domains must be valid hostnames.")
            if cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized

    @field_validator("sender_name", "email_signature", "template_set_name", "auth0_organization", "auth0_connection")
    @classmethod
    def normalize_optional_delivery_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("sso_email_domain")
    @classmethod
    def normalize_optional_sso_domain(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if not _DOMAIN_PATTERN.fullmatch(cleaned):
            raise ValueError("SSO email domain must be a valid hostname.")
        return cleaned

    @field_validator("auth_policy", "preferred_provider")
    @classmethod
    def normalize_optional_delivery_selector(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("enabled_providers")
    @classmethod
    def normalize_enabled_providers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if cleaned in {"local-session", "auth0", "oidc"} and cleaned not in seen:
                seen.add(cleaned)
                normalized.append(cleaned)
        return normalized or None

    @field_validator("sender_email", "reply_to_email")
    @classmethod
    def normalize_optional_delivery_email(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("mail_from_subdomain")
    @classmethod
    def normalize_optional_mail_from(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None


class OrganizationDeliveryActionRequest(BaseModel):
    action: Literal[
        "request_verification",
        "mark_verified",
        "activate_live",
        "reset_domain",
        "send_test",
        "reset_sender",
        "validate_auth_provider",
        "promote_auth_provider_secret",
        "discard_auth_provider_secret",
        "rotate_auth_provider_secret",
    ]
    provider_id: str | None = Field(default=None, max_length=120)

    @field_validator("action")
    @classmethod
    def normalize_delivery_action(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Delivery action cannot be empty.")
        return cleaned

    @field_validator("provider_id")
    @classmethod
    def normalize_delivery_provider_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None


class OrganizationOnboardingUpdateRequest(BaseModel):
    step_key: str = Field(..., min_length=1, max_length=64)
    completed: bool = True

    @field_validator("step_key")
    @classmethod
    def normalize_onboarding_step_key(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "_")
        if not cleaned:
            raise ValueError("Step key cannot be empty.")
        return cleaned


class OrganizationTemplateApplyRequest(BaseModel):
    template_key: str = Field(..., min_length=1, max_length=120)

    @field_validator("template_key")
    @classmethod
    def normalize_template_key(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "_")
        if not cleaned:
            raise ValueError("Template key cannot be empty.")
        return cleaned


class OrganizationApiTokenCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)

    @field_validator("name")
    @classmethod
    def normalize_api_token_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Token name cannot be empty.")
        return cleaned

    @field_validator("scopes")
    @classmethod
    def normalize_api_token_scopes(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized or None


class OrganizationApiTokenRevokeRequest(BaseModel):
    token_id: str = Field(..., min_length=1, max_length=120)

    @field_validator("token_id")
    @classmethod
    def normalize_api_token_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Token id cannot be empty.")
        return cleaned


class OrganizationWebhookCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., min_length=1, max_length=500)
    events: list[str] | None = None

    @field_validator("name")
    @classmethod
    def normalize_webhook_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Webhook name cannot be empty.")
        return cleaned

    @field_validator("url")
    @classmethod
    def normalize_webhook_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Webhook URL cannot be empty.")
        return cleaned

    @field_validator("events")
    @classmethod
    def normalize_webhook_events(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized or None


class OrganizationWebhookActionRequest(BaseModel):
    webhook_id: str = Field(..., min_length=1, max_length=120)
    action: Literal["send_test", "rotate_secret", "pause", "resume", "delete"]

    @field_validator("webhook_id", "action")
    @classmethod
    def normalize_webhook_action_fields(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Webhook action fields cannot be empty.")
        return cleaned


class OrganizationStatusUpdateRequest(BaseModel):
    status: Literal["active", "paused"] = "active"

    @field_validator("status")
    @classmethod
    def normalize_tenant_status(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in {"active", "paused"}:
            raise ValueError("Unsupported tenant status.")
        return cleaned


class OrganizationFeatureFlagUpdateRequest(BaseModel):
    flag_key: str = Field(..., min_length=1, max_length=120)
    enabled: bool | None = None
    limit: int | None = Field(default=None, ge=0)
    reset: bool = False

    @field_validator("flag_key")
    @classmethod
    def normalize_feature_flag_key(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "_")
        if not cleaned:
            raise ValueError("Flag key cannot be empty.")
        return cleaned


class OrganizationTradeAutomationUpdateRequest(BaseModel):
    scope: Literal["personal_paper", "personal_live", "linked"] | None = None
    scope_key: str | None = Field(default=None, max_length=80)
    linked_account_id: str | None = Field(default=None, max_length=36)
    enabled: bool | None = None
    execution_intent: Literal["desk", "broker_paper", "broker_live"] | None = None
    allow_review_candidates: bool | None = None
    tickers: list[str] | None = None
    interval: AllowedInterval | None = None
    horizon: int | None = Field(default=None, ge=1, le=50)
    cycle_interval_seconds: int | None = Field(default=None, ge=15, le=3600)
    cooldown_minutes: int | None = Field(default=None, ge=0, le=1440)
    account_size: float | None = Field(default=None, gt=0)
    effective_funds_multiplier: float | None = Field(default=None, ge=1.0, le=10.0)
    risk_percent: float | None = Field(default=None, gt=0, le=5)
    auto_trade_equities: bool | None = None
    auto_trade_listed_options: bool | None = None
    instrument_type: Literal["equity", "listed_option"] | None = None
    order_type: Literal["market", "limit", "stop_market", "stop_limit", "trailing_stop"] | None = None
    time_in_force: Literal["day", "day_ext", "gtc_90d"] | None = None
    regular_hours_only: bool | None = None
    auto_sync_orders: bool | None = None
    auto_manage_positions: bool | None = None
    auto_flatten_before_close: bool | None = None
    flatten_before_close_minutes: int | None = Field(default=None, ge=1, le=90)
    max_open_positions: int | None = Field(default=None, ge=1, le=25)
    cycle_entry_rank_limit: int | None = Field(default=None, ge=1, le=10)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    max_total_open_notional: float | None = Field(default=None, gt=0)
    max_gross_leverage: float | None = Field(default=None, gt=0)
    max_single_position_pct: float | None = Field(default=None, gt=0, le=100)
    max_correlated_bucket_pct: float | None = Field(default=None, gt=0, le=100)
    max_daily_loss_pct: float | None = Field(default=None, gt=0, le=50)
    max_weekly_loss_pct: float | None = Field(default=None, gt=0, le=50)
    drawdown_size_cut_pct: float | None = Field(default=None, gt=0, le=90)
    drawdown_stop_pct: float | None = Field(default=None, gt=0, le=95)
    drawdown_audit_pct: float | None = Field(default=None, gt=0, le=99)
    risk_cut_multiplier: float | None = Field(default=None, gt=0, le=1)
    min_edge_to_cost_ratio: float | None = Field(default=None, ge=0, le=25)
    market_slippage_bps: float | None = Field(default=None, ge=0, le=500)
    limit_slippage_bps: float | None = Field(default=None, ge=0, le=500)
    max_spread_bps: float | None = Field(default=None, ge=0, le=1000)
    min_average_dollar_volume: float | None = Field(default=None, ge=0)
    max_order_adv_pct: float | None = Field(default=None, gt=0, le=100)
    max_intraday_volume_pct: float | None = Field(default=None, gt=0, le=100)
    no_new_entries_first_minutes: int | None = Field(default=None, ge=0, le=120)
    no_new_entries_before_close_minutes: int | None = Field(default=None, ge=0, le=240)
    max_daily_loss_r: float | None = Field(default=None, gt=0, le=25)
    max_consecutive_losses: int | None = Field(default=None, ge=1, le=25)
    max_daily_entries: int | None = Field(default=None, ge=1, le=100)
    max_daily_entries_per_symbol: int | None = Field(default=None, ge=1, le=25)
    max_error_streak: int | None = Field(default=None, ge=1, le=25)
    long_only: bool | None = None
    equities_only: bool | None = None
    fractional_shares_only: bool | None = None
    use_fast_model: bool | None = None
    allow_pyramiding: bool | None = None
    allow_averaging_down: bool | None = None
    require_liquidity_fields: bool | None = None
    require_edge_fields: bool | None = None
    ai_daily_review_enabled: bool | None = None
    ai_auto_adjust_enabled: bool | None = None
    ai_adjust_live_enabled: bool | None = None
    ai_review_min_trades: int | None = Field(default=None, ge=0, le=100)
    ai_max_daily_setting_changes: int | None = Field(default=None, ge=0, le=12)
    ai_max_step_pct: float | None = Field(default=None, ge=1, le=50)
    accuracy_calibration_enabled: bool | None = None
    accuracy_calibration_apply_to_live: bool | None = None
    accuracy_calibration_min_samples: int | None = Field(default=None, ge=1, le=500)
    accuracy_calibration_stale_after_sessions: int | None = Field(default=None, ge=1, le=60)
    accuracy_calibration_max_candidate_penalty: float | None = Field(default=None, ge=0, le=100)
    daily_objective_enabled: bool | None = None
    daily_profit_target_pct: float | None = Field(default=None, ge=0.1, le=10)
    daily_profit_target_dollars: float | None = Field(default=None, ge=1, le=1_000_000)
    daily_loss_budget_pct: float | None = Field(default=None, ge=0.1, le=10)
    daily_objective_apply_to_live: bool | None = None
    state_control_enabled: bool | None = None
    state_control_auto_throttle_enabled: bool | None = None
    state_control_auto_halt_enabled: bool | None = None
    state_control_watch_score: float | None = Field(default=None, ge=1, le=100)
    state_control_derisk_score: float | None = Field(default=None, ge=1, le=100)
    state_control_halt_score: float | None = Field(default=None, ge=0, le=99)
    state_control_recovery_cycles: int | None = Field(default=None, ge=1, le=20)
    paper_canary_enabled: bool | None = None
    paper_canary_auto_review_enabled: bool | None = None
    paper_canary_window_sessions: int | None = Field(default=None, ge=1, le=20)
    paper_canary_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    live_pilot_soak_enabled: bool | None = None
    live_pilot_max_notional: float | None = Field(default=None, ge=1, le=10)
    live_pilot_symbol: str | None = Field(default=None, min_length=1, max_length=12)
    live_pilot_approval_ttl_minutes: int | None = Field(default=None, ge=1, le=60)
    live_pilot_cancel_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    live_pilot_canary_enabled: bool | None = None
    live_pilot_canary_auto_review_enabled: bool | None = None
    live_pilot_canary_window_sessions: int | None = Field(default=None, ge=1, le=20)
    live_pilot_canary_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    live_pilot_window_canary_enabled: bool | None = None
    live_pilot_window_canary_auto_review_enabled: bool | None = None
    live_pilot_window_canary_window_sessions: int | None = Field(default=None, ge=1, le=20)
    live_pilot_window_canary_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    live_pilot_promotion_report_enabled: bool | None = None
    live_pilot_promotion_report_auto_review_enabled: bool | None = None
    live_pilot_promotion_required_window_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    live_pilot_promotion_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_rollout_enabled: bool | None = None
    limited_live_rollout_max_notional: float | None = Field(default=None, ge=1, le=100)
    limited_live_rollout_max_session_orders: int | None = Field(default=None, ge=1, le=1)
    limited_live_rollout_duration_minutes: int | None = Field(default=None, ge=5, le=240)
    limited_live_rollout_require_limit: bool | None = None
    limited_live_rollout_approval_ttl_minutes: int | None = Field(default=None, ge=1, le=30)
    limited_live_rollout_auto_expand_enabled: bool | None = None
    limited_live_rollout_canary_enabled: bool | None = None
    limited_live_rollout_canary_auto_review_enabled: bool | None = None
    limited_live_rollout_canary_window_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_rollout_canary_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_rollout_canary_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_cap_expansion_report_enabled: bool | None = None
    limited_live_cap_expansion_report_auto_review_enabled: bool | None = None
    limited_live_cap_expansion_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_cap_expansion_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_cap_expansion_target_max_notional: float | None = Field(default=None, ge=1, le=5000)
    limited_live_cap_expansion_enabled: bool | None = None
    limited_live_cap_expansion_max_notional: float | None = Field(default=None, ge=1, le=250)
    limited_live_cap_expansion_duration_minutes: int | None = Field(default=None, ge=5, le=240)
    limited_live_cap_expansion_approval_ttl_minutes: int | None = Field(default=None, ge=1, le=30)
    limited_live_cap_expansion_max_session_orders: int | None = Field(default=None, ge=1, le=1)
    limited_live_cap_expansion_require_limit: bool | None = None
    limited_live_cap_expansion_auto_expand_enabled: bool | None = None
    limited_live_cap_expansion_canary_enabled: bool | None = None
    limited_live_cap_expansion_canary_auto_review_enabled: bool | None = None
    limited_live_cap_expansion_canary_window_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_cap_expansion_canary_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_cap_expansion_canary_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_next_tier_cap_report_enabled: bool | None = None
    limited_live_next_tier_cap_report_auto_review_enabled: bool | None = None
    limited_live_next_tier_cap_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_next_tier_cap_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_next_tier_cap_target_max_notional: float | None = Field(default=None, ge=1, le=10000)
    limited_live_next_tier_cap_enabled: bool | None = None
    limited_live_next_tier_cap_max_notional: float | None = Field(default=None, ge=1, le=500)
    limited_live_next_tier_cap_duration_minutes: int | None = Field(default=None, ge=5, le=240)
    limited_live_next_tier_cap_approval_ttl_minutes: int | None = Field(default=None, ge=1, le=30)
    limited_live_next_tier_cap_max_session_orders: int | None = Field(default=None, ge=1, le=1)
    limited_live_next_tier_cap_require_limit: bool | None = None
    limited_live_next_tier_cap_auto_expand_enabled: bool | None = None
    limited_live_next_tier_cap_canary_enabled: bool | None = None
    limited_live_next_tier_cap_canary_auto_review_enabled: bool | None = None
    limited_live_next_tier_cap_canary_window_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_next_tier_cap_canary_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_next_tier_cap_canary_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_higher_cap_report_enabled: bool | None = None
    limited_live_higher_cap_report_auto_review_enabled: bool | None = None
    limited_live_higher_cap_required_clean_sessions: int | None = Field(default=None, ge=1, le=20)
    limited_live_higher_cap_stale_after_days: int | None = Field(default=None, ge=1, le=30)
    limited_live_higher_cap_target_max_notional: float | None = Field(default=None, ge=1, le=1000)
    limited_live_operator_checklist_required: bool | None = None

    @field_validator("tickers")
    @classmethod
    def normalize_trade_automation_tickers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().upper()
            if not cleaned or cleaned in seen:
                continue
            normalized.append(cleaned)
            seen.add(cleaned)
        return normalized or None

    @field_validator("live_pilot_symbol")
    @classmethod
    def normalize_live_pilot_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().upper()
        return cleaned or None

    @field_validator(
        "scope",
        "execution_intent",
        "instrument_type",
        "order_type",
        "time_in_force",
        mode="before",
    )
    @classmethod
    def normalize_trade_automation_selectors(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("scope_key", "linked_account_id")
    @classmethod
    def normalize_trade_automation_scope_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class OrganizationTradeAutomationActionRequest(BaseModel):
    action: Literal[
        "arm",
        "disarm",
        "kill_switch",
        "clear_kill_switch",
        "run_cycle",
        "reset_from_template",
        "run_ai_review",
        "run_daily_objective_review",
        "run_accuracy_calibration_review",
        "run_loss_containment_review",
        "run_exit_watchdog_review",
        "run_state_control_review",
        "run_state_control_shadow_validation",
        "run_paper_broker_reconciliation",
        "run_paper_order_lifecycle_soak",
        "run_paper_order_lifecycle_canary_review",
        "run_paper_canary_review",
        "run_live_pilot_readiness_review",
        "prepare_live_pilot_soak",
        "run_live_pilot_soak",
        "run_live_pilot_canary_review",
        "prepare_live_pilot_expansion",
        "run_live_pilot_expansion",
        "run_live_pilot_expansion_canary_review",
        "prepare_live_pilot_window",
        "run_live_pilot_window_entry",
        "run_live_pilot_window_exit",
        "run_live_pilot_window_canary_review",
        "run_live_pilot_promotion_report",
        "prepare_limited_live_rollout",
        "activate_limited_live_rollout",
        "rollback_limited_live_rollout",
        "run_limited_live_rollout_canary_review",
        "run_limited_live_cap_expansion_report",
        "prepare_limited_live_cap_expansion",
        "activate_limited_live_cap_expansion",
        "rollback_limited_live_cap_expansion",
        "run_limited_live_cap_expansion_canary_review",
        "run_limited_live_next_tier_cap_report",
        "prepare_limited_live_next_tier_cap",
        "activate_limited_live_next_tier_cap",
        "rollback_limited_live_next_tier_cap",
        "run_limited_live_next_tier_cap_canary_review",
        "run_limited_live_broker_reconciliation",
        "run_limited_live_session_closeout",
        "run_limited_live_higher_cap_report",
        "submit_limited_live_operator_checklist",
    ]
    scope: Literal["personal_paper", "personal_live", "linked"] | None = None
    scope_key: str | None = Field(default=None, max_length=80)
    linked_account_id: str | None = Field(default=None, max_length=36)
    checklist: dict[str, Any] | None = None

    @field_validator("action", "scope")
    @classmethod
    def normalize_trade_automation_action(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Automation action cannot be empty.")
        return cleaned

    @field_validator("scope_key", "linked_account_id")
    @classmethod
    def normalize_trade_automation_action_scope_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class StrategyDeskUpdateRequest(BaseModel):
    enabled: bool | None = None
    paper_trading_enabled: bool | None = None
    lifecycle_stage: Literal["research", "paper", "validated"] | None = None
    trading_mode: Literal["research", "paper"] | None = None
    config: dict[str, Any] | None = None


class StrategyDeskRunRequest(BaseModel):
    run_type: Literal["manual", "scheduled", "replay"] = "manual"

    @field_validator("run_type")
    @classmethod
    def normalize_strategy_run_type(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Run type cannot be empty.")
        return cleaned


class BacktestRunRequest(BaseModel):
    desk_key: str = Field(..., min_length=1, max_length=64)
    horizon_days: int = Field(5, ge=1, le=60)
    warmup_bars: int = Field(60, ge=20, le=400)
    fee_bps: float = Field(2.0, ge=0, le=1000)
    slippage_bps: float = Field(5.0, ge=0, le=1000)

    @field_validator("desk_key")
    @classmethod
    def normalize_backtest_desk_key(cls, value: str) -> str:
        cleaned = value.strip().lower().replace(" ", "_")
        if not cleaned:
            raise ValueError("Desk key cannot be empty.")
        return cleaned


class PortfolioTargetExecutionRequest(BaseModel):
    portfolio_target_run_id: str | None = Field(default=None, max_length=36)
    execution_intent: Literal["broker_paper"] = "broker_paper"
    dry_run: bool = False

    @field_validator("portfolio_target_run_id")
    @classmethod
    def normalize_portfolio_target_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AiDeskAction(BaseModel):
    key: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=160)
    detail: str | None = Field(default=None, max_length=800)
    priority: int = Field(50, ge=0, le=100)
    stage: Literal["command_center", "trade_planner", "paper_execution", "supervised_live"] = "command_center"
    tone: Literal["positive", "warning", "negative", "neutral"] = "neutral"
    desk_key: str | None = Field(default=None, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class AiPolicyDecision(BaseModel):
    action: str = Field(..., min_length=1, max_length=120)
    allowed: bool = False
    policy_version: str = Field("v1", min_length=1, max_length=32)
    policy_digest: str = Field(..., min_length=8, max_length=80)
    blockers: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class AiAgentStatus(BaseModel):
    key: str = Field(..., min_length=1, max_length=80)
    label: str = Field(..., min_length=1, max_length=120)
    status: Literal["idle", "running", "ready", "blocked", "completed", "failed", "skipped", "warning"] = "idle"
    detail: str | None = Field(default=None, max_length=800)
    blockers: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class AiDeskPolicyManifest(BaseModel):
    version: str = Field("v1", min_length=1, max_length=32)
    enabled: bool = False
    armed: bool = False
    kill_switch: bool = False
    autonomy_boundary: Literal["paper_plus_live_intent"] = "paper_plus_live_intent"
    allowed_desks: list[str] = Field(default_factory=lambda: ["macro_trend", "stat_arb"], min_length=1, max_length=12)
    allowed_instrument_types: list[Literal["equity"]] = Field(default_factory=lambda: ["equity"])
    allowed_sides: list[Literal["buy"]] = Field(default_factory=lambda: ["buy"])
    allowed_order_types: list[Literal["limit"]] = Field(default_factory=lambda: ["limit"])
    allow_paper_execution: bool = True
    allow_live_intents: bool = True
    allow_live_submit: bool = False
    equities_only: bool = True
    long_only: bool = True
    limit_orders_only: bool = True
    regular_hours_only: bool = True
    max_risk_percent: float = Field(0.5, gt=0, le=5)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    stale_run_minutes: int = Field(1440, ge=5, le=10080)
    cycle_interval_minutes: int = Field(15, ge=1, le=1440)
    updated_at: str | None = None
    updated_by: str | None = Field(default=None, max_length=255)

    @field_validator("allowed_desks")
    @classmethod
    def normalize_policy_desks(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)
        if not normalized:
            raise ValueError("At least one strategy desk must be allowed.")
        return normalized

    @field_validator("allow_live_submit")
    @classmethod
    def block_policy_live_submit(cls, value: bool) -> bool:
        if bool(value):
            raise ValueError("Autonomous live order submission is not supported.")
        return False


class AiDeskPolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    armed: bool | None = None
    kill_switch: bool | None = None
    allowed_desks: list[str] | None = Field(default=None, min_length=1, max_length=12)
    allow_paper_execution: bool | None = None
    allow_live_intents: bool | None = None
    allow_live_submit: bool | None = None
    max_risk_percent: float | None = Field(default=None, gt=0, le=5)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    stale_run_minutes: int | None = Field(default=None, ge=5, le=10080)
    cycle_interval_minutes: int | None = Field(default=None, ge=1, le=1440)

    @field_validator("allowed_desks")
    @classmethod
    def normalize_policy_update_desks(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        return AiDeskPolicyManifest.normalize_policy_desks(values)

    @field_validator("allow_live_submit")
    @classmethod
    def block_policy_update_live_submit(cls, value: bool | None) -> bool | None:
        if bool(value):
            raise ValueError("Autonomous live order submission is not supported.")
        return False if value is not None else value


class AiDeskControlRequest(BaseModel):
    action: Literal["enable", "disable", "arm", "disarm", "kill_switch", "clear_kill_switch", "queue_cycle"]
    reason: str | None = Field(default=None, max_length=400)

    @field_validator("reason")
    @classmethod
    def normalize_control_reason(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AiAutonomousCycleRequest(BaseModel):
    trigger: Literal["manual", "scheduled"] = "manual"
    enqueue: bool = False
    dry_run: bool = False


class AiAutonomousCycleResult(BaseModel):
    cycle_id: str = Field(..., min_length=1, max_length=80)
    status: Literal["queued", "blocked", "completed", "failed"] = "blocked"
    trigger: Literal["manual", "scheduled"] = "manual"
    dry_run: bool = False
    policy_digest: str = Field(..., min_length=8, max_length=80)
    decisions: list[AiPolicyDecision] = Field(default_factory=list)
    agents: list[AiAgentStatus] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    next_scheduled_run: str | None = None


class AiDeskManagerSnapshot(BaseModel):
    stage: Literal["ai_desk_manager"] = "ai_desk_manager"
    status: Literal["ready", "watch", "blocked", "stale"] = "watch"
    generated_at: str | None = None
    command_center: dict[str, Any] = Field(default_factory=dict)
    desk_states: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[AiDeskAction] = Field(default_factory=list)
    trade_planner: dict[str, Any] = Field(default_factory=dict)
    paper_execution: dict[str, Any] = Field(default_factory=dict)
    live_gate: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    policy_digest: str | None = None
    autonomy: dict[str, Any] = Field(default_factory=dict)
    agents: list[AiAgentStatus] = Field(default_factory=list)
    latest_cycle: dict[str, Any] = Field(default_factory=dict)
    active_blockers: list[str] = Field(default_factory=list)
    next_scheduled_run: str | None = None


class AiTradePlanRequest(BaseModel):
    ticker: str | None = Field(default=None, max_length=24)
    target_symbol: str | None = Field(default=None, max_length=24)
    desk_key: str | None = Field(default=None, max_length=80)
    portfolio_target_run_id: str | None = Field(default=None, max_length=36)
    linked_account_id: str | None = Field(default=None, max_length=36)
    execution_intent: Literal["desk", "broker_paper", "broker_live"] = "desk"
    interval: AllowedInterval = "5m"
    horizon: int = Field(5, ge=1, le=50)
    account_size: float = Field(10000.0, gt=0)
    risk_percent: float = Field(0.5, gt=0, le=5)
    live_price: float | None = Field(default=None, gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    create_intent: bool = False

    @field_validator("ticker", "target_symbol")
    @classmethod
    def normalize_ai_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().upper()
        return cleaned or None

    @field_validator("desk_key", "portfolio_target_run_id", "linked_account_id")
    @classmethod
    def normalize_ai_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AiTradePlanPreview(BaseModel):
    allowed: bool = False
    blocked: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    open_trade_request: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] | None = None
    trade_intent: dict[str, Any] | None = None


class AiPaperExecutionRequest(BaseModel):
    portfolio_target_run_id: str | None = Field(default=None, max_length=36)
    dry_run: bool = False

    @field_validator("portfolio_target_run_id")
    @classmethod
    def normalize_ai_paper_run_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class AiLiveIntentRequest(BaseModel):
    ticker: str | None = Field(default=None, max_length=24)
    target_symbol: str | None = Field(default=None, max_length=24)
    desk_key: str | None = Field(default=None, max_length=80)
    linked_account_id: str | None = Field(default=None, max_length=36)
    account_size: float = Field(10000.0, gt=0)
    risk_percent: float = Field(0.5, gt=0, le=5)
    live_price: float | None = Field(default=None, gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    frontend_confirmation: bool = False

    @field_validator("ticker", "target_symbol")
    @classmethod
    def normalize_ai_live_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().upper()
        return cleaned or None

    @field_validator("desk_key", "linked_account_id")
    @classmethod
    def normalize_ai_live_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class OptionsAutomationScanRequest(BaseModel):
    tickers: list[str] | None = None
    limit: int = Field(30, ge=1, le=250)
    force: bool = False
    automation_trigger: str | None = Field(default=None, max_length=24)

    @field_validator("tickers")
    @classmethod
    def normalize_options_scan_tickers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().upper()
            if cleaned and cleaned not in seen:
                normalized.append(cleaned)
                seen.add(cleaned)
        return normalized or None

    @field_validator("automation_trigger")
    @classmethod
    def normalize_options_scan_trigger(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        if cleaned not in {"manual", "scheduled"}:
            raise ValueError("Automation trigger must be manual or scheduled.")
        return cleaned


class OptionsAutomationExecuteRequest(BaseModel):
    scan_run_id: str | None = Field(default=None, max_length=36)
    contract_symbol: str | None = Field(default=None, max_length=80)
    max_candidates: int = Field(1, ge=1, le=5)
    dry_run: bool = False
    automation_trigger: str | None = Field(default=None, max_length=24)

    @field_validator("scan_run_id", "contract_symbol")
    @classmethod
    def normalize_options_execute_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if value is not None and cleaned and len(cleaned) <= 80:
            cleaned = cleaned.upper() if cleaned.replace(" ", "").isalnum() else cleaned
        return cleaned or None

    @field_validator("automation_trigger")
    @classmethod
    def normalize_options_execute_trigger(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        if cleaned not in {"manual", "scheduled"}:
            raise ValueError("Automation trigger must be manual or scheduled.")
        return cleaned


class OptionsAutomationRefreshRequest(BaseModel):
    trade_id: str | None = Field(default=None, max_length=80)
    contract_symbol: str | None = Field(default=None, max_length=80)
    automation_trigger: str | None = Field(default=None, max_length=24)

    @field_validator("trade_id", "contract_symbol")
    @classmethod
    def normalize_options_refresh_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if cleaned and len(cleaned) <= 80:
            cleaned = cleaned.upper() if cleaned.replace(" ", "").isalnum() else cleaned
        return cleaned or None

    @field_validator("automation_trigger")
    @classmethod
    def normalize_options_refresh_trigger(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        if cleaned not in {"manual", "scheduled"}:
            raise ValueError("Automation trigger must be manual or scheduled.")
        return cleaned


class OptionsAutomationCloseRequest(BaseModel):
    trade_id: str | None = Field(default=None, max_length=80)
    contract_symbol: str | None = Field(default=None, max_length=80)
    close_fraction: float = Field(1.0, gt=0, le=1)
    automation_trigger: str | None = Field(default=None, max_length=24)

    @field_validator("trade_id", "contract_symbol")
    @classmethod
    def normalize_options_close_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        if cleaned and len(cleaned) <= 80:
            cleaned = cleaned.upper() if cleaned.replace(" ", "").isalnum() else cleaned
        return cleaned or None

    @field_validator("automation_trigger")
    @classmethod
    def normalize_options_close_trigger(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip().lower()
        if cleaned not in {"manual", "scheduled"}:
            raise ValueError("Automation trigger must be manual or scheduled.")
        return cleaned


class BillingPlanChangeRequest(BaseModel):
    plan_key: str = Field(..., min_length=1, max_length=64)

    @field_validator("plan_key")
    @classmethod
    def normalize_plan_key(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Plan key cannot be empty.")
        return cleaned


class BillingCheckoutRequest(BaseModel):
    plan_key: str = Field(..., min_length=1, max_length=64)
    billing_cycle: Literal["monthly", "annual"] = "monthly"
    success_url: str | None = Field(default=None, max_length=500)
    cancel_url: str | None = Field(default=None, max_length=500)

    @field_validator("plan_key")
    @classmethod
    def normalize_checkout_plan_key(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Plan key cannot be empty.")
        return cleaned

    @field_validator("success_url", "cancel_url")
    @classmethod
    def normalize_checkout_urls(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class BillingRecoveryRequest(BaseModel):
    action: Literal["reconcile", "retry_last_failure", "sync_entitlements"]

    @field_validator("action")
    @classmethod
    def normalize_billing_recovery_action(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("Recovery action cannot be empty.")
        return cleaned
