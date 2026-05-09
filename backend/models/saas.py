from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="active")
    plan_key: Mapped[str] = mapped_column(String(64), default="starter")
    billing_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    brand_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    invitations: Mapped[list["TenantInvitation"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    subscriptions: Mapped[list["SubscriptionRecord"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    entitlements: Mapped[list["EntitlementRecord"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    billing_events: Mapped[list["BillingEventRecord"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    order_events: Mapped[list["OrderEventRecord"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    async_jobs: Mapped[list["AsyncJob"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    brokerage_linked_accounts: Mapped[list["BrokerageLinkedAccount"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    trade_approval_intents: Mapped[list["TradeApprovalIntent"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    strategy_desks: Mapped[list["StrategyDesk"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    strategy_runs: Mapped[list["StrategyRun"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    strategy_target_publications: Mapped[list["StrategyTargetPublication"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    portfolio_target_runs: Mapped[list["PortfolioTargetRun"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    portfolio_target_execution_runs: Mapped[list["PortfolioTargetExecutionRun"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    option_automation_scan_runs: Mapped[list["OptionAutomationScanRun"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    domain_events: Mapped[list["DomainEventLog"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    desk_pnl_snapshots: Mapped[list["DeskPnlSnapshot"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    strategy_versions: Mapped[list["StrategyVersion"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    strategy_deployments: Mapped[list["StrategyDeployment"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    strategy_promotion_gates: Mapped[list["StrategyPromotionGate"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    trade_decisions: Mapped[list["TradeDecision"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    decision_replay_events: Mapped[list["DecisionReplayEvent"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    readiness_snapshots: Mapped[list["ReadinessSnapshot"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    readiness_blockers: Mapped[list["ReadinessBlocker"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    risk_policies: Mapped[list["RiskPolicy"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    risk_events: Mapped[list["RiskEvent"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    execution_quality_snapshots: Mapped[list["ExecutionQualitySnapshot"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    audit_exports: Mapped[list["AuditExport"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    entitlement_usage: Mapped[list["EntitlementUsage"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    live_trading_authorizations: Mapped[list["LiveTradingAuthorization"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    live_trading_sessions: Mapped[list["LiveTradingSession"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    live_order_intents: Mapped[list["LiveOrderIntent"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    live_risk_checks: Mapped[list["LiveRiskCheck"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    broker_execution_receipts: Mapped[list["BrokerExecutionReceipt"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    live_kill_switch_events: Mapped[list["LiveKillSwitchEvent"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auth_subject: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(64), default="local-demo")
    platform_role: Mapped[str] = mapped_column(String(32), default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_json: Mapped[dict] = mapped_column("profile", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sent_invitations: Mapped[list["TenantInvitation"]] = relationship(
        back_populates="inviter_user",
        cascade="all, delete-orphan",
        foreign_keys="TenantInvitation.inviter_user_id",
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    owned_brokerage_accounts: Mapped[list["BrokerageLinkedAccount"]] = relationship(
        back_populates="owner_user",
        cascade="all, delete-orphan",
        foreign_keys="BrokerageLinkedAccount.owner_user_id",
    )
    requested_trade_approval_intents: Mapped[list["TradeApprovalIntent"]] = relationship(
        back_populates="requester_user",
        cascade="all, delete-orphan",
        foreign_keys="TradeApprovalIntent.requester_user_id",
    )
    approved_trade_approval_intents: Mapped[list["TradeApprovalIntent"]] = relationship(
        back_populates="approver_user",
        cascade="all, delete-orphan",
        foreign_keys="TradeApprovalIntent.approver_user_id",
    )
    created_strategy_versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="created_by_user",
        foreign_keys="StrategyVersion.created_by_user_id",
    )
    approved_strategy_promotion_gates: Mapped[list["StrategyPromotionGate"]] = relationship(
        back_populates="approved_by_user",
        foreign_keys="StrategyPromotionGate.approved_by_user_id",
    )
    requested_audit_exports: Mapped[list["AuditExport"]] = relationship(
        back_populates="requested_by_user",
        foreign_keys="AuditExport.requested_by_user_id",
    )
    live_trading_authorizations: Mapped[list["LiveTradingAuthorization"]] = relationship(
        back_populates="user",
        foreign_keys="LiveTradingAuthorization.user_id",
    )
    approved_live_order_intents: Mapped[list["LiveOrderIntent"]] = relationship(
        back_populates="approved_by_user",
        foreign_keys="LiveOrderIntent.approved_by_user_id",
    )
    triggered_live_kill_switch_events: Mapped[list["LiveKillSwitchEvent"]] = relationship(
        back_populates="triggered_by_user",
        foreign_keys="LiveKillSwitchEvent.triggered_by_user_id",
    )
    cleared_live_kill_switch_events: Mapped[list["LiveKillSwitchEvent"]] = relationship(
        back_populates="cleared_by_user",
        foreign_keys="LiveKillSwitchEvent.cleared_by_user_id",
    )


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="owner")
    status: Mapped[str] = mapped_column(String(24), default="active")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class TenantInvitation(Base):
    __tablename__ = "tenant_invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    inviter_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    invite_token: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="invitations")
    inviter_user: Mapped["User | None"] = relationship(
        back_populates="sent_invitations",
        foreign_keys=[inviter_user_id],
    )


class SubscriptionRecord(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="stripe")
    status: Mapped[str] = mapped_column(String(32), default="inactive")
    plan_key: Mapped[str] = mapped_column(String(64), default="starter")
    external_customer_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    external_subscription_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="subscriptions")


class EntitlementRecord(Base):
    __tablename__ = "entitlements"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_tenant_entitlement"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    limit_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="plan")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="entitlements")


class BillingEventRecord(Base):
    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="internal-demo", index=True)
    source: Mapped[str] = mapped_column(String(32), default="system", index=True)
    event_key: Mapped[str] = mapped_column(String(160), index=True)
    external_event_id: Mapped[str | None] = mapped_column(String(191), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)
    plan_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    billing_cycle: Mapped[str | None] = mapped_column(String(24), nullable=True)
    external_customer_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    external_subscription_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    result_json: Mapped[dict] = mapped_column("result", JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="billing_events")


class OrderEventRecord(Base):
    __tablename__ = "order_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    event_key: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)
    order_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    time_in_force: Mapped[str | None] = mapped_column(String(32), nullable=True)
    route_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    book_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="order_events")
    trade_decisions: Mapped[list["TradeDecision"]] = relationship(back_populates="order_event")
    execution_quality_snapshots: Mapped[list["ExecutionQualitySnapshot"]] = relationship(back_populates="order_event")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="audit_events")
    user: Mapped["User | None"] = relationship(back_populates="audit_events")


class BrokerageLinkedAccount(Base):
    __tablename__ = "brokerage_linked_accounts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_account_id",
            "account_environment",
            name="uq_brokerage_linked_account_external",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="alpaca", index=True)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    account_environment: Mapped[str] = mapped_column(String(24), default="paper", index=True)
    connection_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    token_health: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    approval_policy: Mapped[str] = mapped_column(String(32), default="approval_required")
    oauth_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    external_account_number_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    linked_identity_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="brokerage_linked_accounts")
    owner_user: Mapped["User"] = relationship(
        back_populates="owned_brokerage_accounts",
        foreign_keys=[owner_user_id],
    )
    trade_intents: Mapped[list["TradeApprovalIntent"]] = relationship(
        back_populates="linked_account",
        cascade="all, delete-orphan",
    )
    strategy_deployments: Mapped[list["StrategyDeployment"]] = relationship(back_populates="linked_account")
    live_trading_authorizations: Mapped[list["LiveTradingAuthorization"]] = relationship(back_populates="linked_account")
    live_trading_sessions: Mapped[list["LiveTradingSession"]] = relationship(back_populates="linked_account")


class TradeApprovalIntent(Base):
    __tablename__ = "trade_approval_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    requester_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    approver_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    linked_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("brokerage_linked_accounts.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="alpaca", index=True)
    execution_lane: Mapped[str] = mapped_column(String(32), default="linked_client", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_approval", index=True)
    ticker: Mapped[str] = mapped_column(String(24), index=True)
    instrument_type: Mapped[str] = mapped_column(String(32), default="listed_option")
    account_environment: Mapped[str] = mapped_column(String(24), default="paper", index=True)
    trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    route_correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_payload_json: Mapped[dict] = mapped_column("request_payload", JSON, default=dict)
    analysis_json: Mapped[dict] = mapped_column("analysis", JSON, default=dict)
    position_json: Mapped[dict] = mapped_column("position", JSON, default=dict)
    order_ticket_json: Mapped[dict] = mapped_column("order_ticket", JSON, default=dict)
    broker_submit_payload_json: Mapped[dict] = mapped_column("broker_submit_payload", JSON, default=dict)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    broker_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    broker_response_json: Mapped[dict] = mapped_column("broker_response", JSON, default=dict)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="trade_approval_intents")
    requester_user: Mapped["User"] = relationship(
        back_populates="requested_trade_approval_intents",
        foreign_keys=[requester_user_id],
    )
    approver_user: Mapped["User | None"] = relationship(
        back_populates="approved_trade_approval_intents",
        foreign_keys=[approver_user_id],
    )
    linked_account: Mapped["BrokerageLinkedAccount"] = relationship(back_populates="trade_intents")
    trade_decisions: Mapped[list["TradeDecision"]] = relationship(back_populates="trade_approval_intent")


class AsyncJob(Base):
    __tablename__ = "async_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    result_json: Mapped[dict] = mapped_column("result", JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="async_jobs")


class StrategyDesk(Base):
    __tablename__ = "strategy_desks"
    __table_args__ = (UniqueConstraint("tenant_id", "desk_key", name="uq_strategy_desk_tenant_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    desk_key: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(64), default="research")
    lifecycle_stage: Mapped[str] = mapped_column(String(32), default="research")
    run_mode: Mapped[str] = mapped_column(String(32), default="manual")
    trading_mode: Mapped[str] = mapped_column(String(32), default="research")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paper_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[dict] = mapped_column("config", JSON, default=dict)
    runtime_json: Mapped[dict] = mapped_column("runtime", JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="strategy_desks")
    strategy_runs: Mapped[list["StrategyRun"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    target_publications: Mapped[list["StrategyTargetPublication"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    desk_pnl_snapshots: Mapped[list["DeskPnlSnapshot"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    strategy_versions: Mapped[list["StrategyVersion"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    strategy_deployments: Mapped[list["StrategyDeployment"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    strategy_promotion_gates: Mapped[list["StrategyPromotionGate"]] = relationship(back_populates="strategy_desk", cascade="all, delete-orphan")
    trade_decisions: Mapped[list["TradeDecision"]] = relationship(back_populates="strategy_desk")
    readiness_snapshots: Mapped[list["ReadinessSnapshot"]] = relationship(back_populates="strategy_desk")
    readiness_blockers: Mapped[list["ReadinessBlocker"]] = relationship(back_populates="strategy_desk")
    risk_policies: Mapped[list["RiskPolicy"]] = relationship(back_populates="strategy_desk")
    risk_events: Mapped[list["RiskEvent"]] = relationship(back_populates="strategy_desk")
    execution_quality_snapshots: Mapped[list["ExecutionQualitySnapshot"]] = relationship(back_populates="strategy_desk")
    live_trading_authorizations: Mapped[list["LiveTradingAuthorization"]] = relationship(back_populates="strategy_desk")
    live_trading_sessions: Mapped[list["LiveTradingSession"]] = relationship(back_populates="strategy_desk")
    live_order_intents: Mapped[list["LiveOrderIntent"]] = relationship(back_populates="strategy_desk")
    live_risk_checks: Mapped[list["LiveRiskCheck"]] = relationship(back_populates="strategy_desk")
    live_kill_switch_events: Mapped[list["LiveKillSwitchEvent"]] = relationship(back_populates="strategy_desk")


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    desk_key: Mapped[str] = mapped_column(String(64), index=True)
    run_type: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    timeframe_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    market_state_json: Mapped[dict] = mapped_column("market_state", JSON, default=dict)
    features_json: Mapped[dict] = mapped_column("features", JSON, default=dict)
    signal_json: Mapped[dict] = mapped_column("signal", JSON, default=dict)
    targets_json: Mapped[dict] = mapped_column("targets", JSON, default=dict)
    validation_json: Mapped[dict] = mapped_column("validation", JSON, default=dict)
    metrics_json: Mapped[dict] = mapped_column("metrics", JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="strategy_runs")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="strategy_runs")
    target_publications: Mapped[list["StrategyTargetPublication"]] = relationship(back_populates="strategy_run", cascade="all, delete-orphan")
    trade_decisions: Mapped[list["TradeDecision"]] = relationship(back_populates="strategy_run")
    readiness_snapshots: Mapped[list["ReadinessSnapshot"]] = relationship(back_populates="strategy_run")


class StrategyTargetPublication(Base):
    __tablename__ = "strategy_target_publications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    strategy_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    desk_key: Mapped[str] = mapped_column(String(64), index=True)
    publication_kind: Mapped[str] = mapped_column(String(32), default="desk_targets")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    required_capital: Mapped[float] = mapped_column(Float, default=0.0)
    expected_holding_period: Mapped[str | None] = mapped_column(String(64), nullable=True)
    targets_json: Mapped[dict] = mapped_column("targets", JSON, default=dict)
    metrics_json: Mapped[dict] = mapped_column("metrics", JSON, default=dict)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="strategy_target_publications")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="target_publications")
    strategy_run: Mapped["StrategyRun | None"] = relationship(back_populates="target_publications")


class PortfolioTargetRun(Base):
    __tablename__ = "portfolio_target_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    source_run_ids_json: Mapped[dict] = mapped_column("source_run_ids", JSON, default=dict)
    desk_inputs_json: Mapped[dict] = mapped_column("desk_inputs", JSON, default=dict)
    allocator_config_json: Mapped[dict] = mapped_column("allocator_config", JSON, default=dict)
    portfolio_targets_json: Mapped[dict] = mapped_column("portfolio_targets", JSON, default=dict)
    order_plan_json: Mapped[dict] = mapped_column("order_plan", JSON, default=dict)
    metrics_json: Mapped[dict] = mapped_column("metrics", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="portfolio_target_runs")
    execution_runs: Mapped[list["PortfolioTargetExecutionRun"]] = relationship(back_populates="portfolio_target_run", cascade="all, delete-orphan")


class PortfolioTargetExecutionRun(Base):
    __tablename__ = "portfolio_target_execution_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    portfolio_target_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("portfolio_target_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    execution_intent: Mapped[str] = mapped_column(String(32), default="broker_paper", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    working_count: Mapped[int] = mapped_column(Integer, default=0)
    partial_fill_count: Mapped[int] = mapped_column(Integer, default=0)
    filled_count: Mapped[int] = mapped_column(Integer, default=0)
    canceled_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    orphan_event_count: Mapped[int] = mapped_column(Integer, default=0)
    summary_json: Mapped[dict] = mapped_column("summary", JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="portfolio_target_execution_runs")
    portfolio_target_run: Mapped["PortfolioTargetRun | None"] = relationship(back_populates="execution_runs")
    execution_items: Mapped[list["PortfolioTargetExecutionItem"]] = relationship(back_populates="execution_run", cascade="all, delete-orphan")


class PortfolioTargetExecutionItem(Base):
    __tablename__ = "portfolio_target_execution_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("portfolio_target_execution_runs.id", ondelete="CASCADE"),
        index=True,
    )
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    action: Mapped[str] = mapped_column(String(24), default="skip", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    requested_target_weight: Mapped[float] = mapped_column(Float, default=0.0)
    requested_target_notional: Mapped[float] = mapped_column(Float, default=0.0)
    requested_delta_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    resolved_route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_desk_key: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    submitted_trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    submitted_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    broker_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    remaining_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    average_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reconciliation_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata_json: Mapped[dict] = mapped_column("source_metadata", JSON, default=dict)
    result_json: Mapped[dict] = mapped_column("result", JSON, default=dict)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    execution_run: Mapped["PortfolioTargetExecutionRun"] = relationship(back_populates="execution_items")


class OptionAutomationScanRun(Base):
    __tablename__ = "option_automation_scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    feed: Mapped[str] = mapped_column(String(32), default="opra", index=True)
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, default=30)
    ticker_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    ready_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_tickers_json: Mapped[dict] = mapped_column("requested_tickers", JSON, default=dict)
    candidates_json: Mapped[dict] = mapped_column("candidates", JSON, default=dict)
    summary_json: Mapped[dict] = mapped_column("summary", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="option_automation_scan_runs")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    desk_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    request_json: Mapped[dict] = mapped_column("request", JSON, default=dict)
    summary_json: Mapped[dict] = mapped_column("summary", JSON, default=dict)
    artifacts_json: Mapped[dict] = mapped_column("artifacts", JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="backtest_runs")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="backtest_runs")


class DomainEventLog(Base):
    __tablename__ = "domain_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), default="system", index=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant | None"] = relationship(back_populates="domain_events")


class DeskPnlSnapshot(Base):
    __tablename__ = "desk_pnl_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    desk_key: Mapped[str] = mapped_column(String(64), index=True)
    gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    net_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_json: Mapped[dict] = mapped_column("metrics", JSON, default=dict)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="desk_pnl_snapshots")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="desk_pnl_snapshots")


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "strategy_desk_id", "version_number", name="uq_strategy_version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="internal")
    source_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    config_json: Mapped[dict] = mapped_column("config", JSON, default=dict)
    risk_profile_json: Mapped[dict] = mapped_column("risk_profile", JSON, default=dict)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="strategy_versions")
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="strategy_versions")
    created_by_user: Mapped["User | None"] = relationship(back_populates="created_strategy_versions", foreign_keys=[created_by_user_id])
    deployments: Mapped[list["StrategyDeployment"]] = relationship(back_populates="strategy_version", cascade="all, delete-orphan")
    live_trading_authorizations: Mapped[list["LiveTradingAuthorization"]] = relationship(back_populates="strategy_version")
    live_trading_sessions: Mapped[list["LiveTradingSession"]] = relationship(back_populates="strategy_version")


class StrategyDeployment(Base):
    __tablename__ = "strategy_deployments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True)
    linked_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("brokerage_linked_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(24), default="paper", index=True)
    status: Mapped[str] = mapped_column(String(32), default="stopped", index=True)
    allocation_cap: Mapped[float] = mapped_column(Float, default=0.0)
    max_order_notional: Mapped[float] = mapped_column(Float, default=0.0)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="strategy_deployments")
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="strategy_deployments")
    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="deployments")
    linked_account: Mapped["BrokerageLinkedAccount | None"] = relationship(back_populates="strategy_deployments")


class StrategyPromotionGate(Base):
    __tablename__ = "strategy_promotion_gates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    from_stage: Mapped[str] = mapped_column(String(32))
    to_stage: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    required_score: Mapped[int] = mapped_column(Integer)
    actual_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirements_json: Mapped[dict] = mapped_column("requirements", JSON, default=dict)
    blockers_json: Mapped[dict] = mapped_column("blockers", JSON, default=dict)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="strategy_promotion_gates")
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="strategy_promotion_gates")
    approved_by_user: Mapped["User | None"] = relationship(back_populates="approved_strategy_promotion_gates", foreign_keys=[approved_by_user_id])


class TradeDecision(Base):
    __tablename__ = "trade_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    strategy_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    trade_approval_intent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("trade_approval_intents.id", ondelete="SET NULL"), nullable=True, index=True)
    order_event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("order_events.id", ondelete="SET NULL"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    instrument_type: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    decision_status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_snapshot_json: Mapped[dict] = mapped_column("signal_snapshot", JSON, default=dict)
    risk_snapshot_json: Mapped[dict] = mapped_column("risk_snapshot", JSON, default=dict)
    readiness_snapshot_json: Mapped[dict] = mapped_column("readiness_snapshot", JSON, default=dict)
    market_snapshot_json: Mapped[dict] = mapped_column("market_snapshot", JSON, default=dict)
    broker_snapshot_json: Mapped[dict] = mapped_column("broker_snapshot", JSON, default=dict)
    decision_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="trade_decisions")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="trade_decisions")
    strategy_run: Mapped["StrategyRun | None"] = relationship(back_populates="trade_decisions")
    trade_approval_intent: Mapped["TradeApprovalIntent | None"] = relationship(back_populates="trade_decisions")
    order_event: Mapped["OrderEventRecord | None"] = relationship(back_populates="trade_decisions")
    replay_events: Mapped[list["DecisionReplayEvent"]] = relationship(back_populates="trade_decision", cascade="all, delete-orphan")
    risk_events: Mapped[list["RiskEvent"]] = relationship(back_populates="trade_decision")
    live_order_intents: Mapped[list["LiveOrderIntent"]] = relationship(back_populates="trade_decision")


class DecisionReplayEvent(Base):
    __tablename__ = "decision_replay_events"
    __table_args__ = (UniqueConstraint("trade_decision_id", "sequence_number", name="uq_decision_replay_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    trade_decision_id: Mapped[str] = mapped_column(String(36), ForeignKey("trade_decisions.id", ondelete="CASCADE"), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="decision_replay_events")
    trade_decision: Mapped["TradeDecision"] = relationship(back_populates="replay_events")


class ReadinessSnapshot(Base):
    __tablename__ = "readiness_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    strategy_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    components_json: Mapped[dict] = mapped_column("components", JSON, default=dict)
    hard_blockers_json: Mapped[dict] = mapped_column("hard_blockers", JSON, default=dict)
    warnings_json: Mapped[dict] = mapped_column("warnings", JSON, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="readiness_snapshots")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="readiness_snapshots")
    strategy_run: Mapped["StrategyRun | None"] = relationship(back_populates="readiness_snapshots")
    blockers: Mapped[list["ReadinessBlocker"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class ReadinessBlocker(Base):
    __tablename__ = "readiness_blockers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    readiness_snapshot_id: Mapped[str] = mapped_column(String(36), ForeignKey("readiness_snapshots.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    blocker_key: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(24), index=True)
    source: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="readiness_blockers")
    snapshot: Mapped["ReadinessSnapshot"] = relationship(back_populates="blockers")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="readiness_blockers")


class RiskPolicy(Base):
    __tablename__ = "risk_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=0.0)
    max_weekly_loss: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_position_notional: Mapped[float] = mapped_column(Float, default=0.0)
    max_order_notional: Mapped[float] = mapped_column(Float, default=0.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=0)
    allowed_symbols_json: Mapped[dict] = mapped_column("allowed_symbols", JSON, default=dict)
    blocked_symbols_json: Mapped[dict] = mapped_column("blocked_symbols", JSON, default=dict)
    allowed_instruments_json: Mapped[dict] = mapped_column("allowed_instruments", JSON, default=dict)
    requires_approval_above: Mapped[float | None] = mapped_column(Float, nullable=True)
    config_json: Mapped[dict] = mapped_column("config", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="risk_policies")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="risk_policies")
    live_risk_checks: Mapped[list["LiveRiskCheck"]] = relationship(back_populates="risk_policy")


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    trade_decision_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("trade_decisions.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(24), index=True)
    breached_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_taken: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="risk_events")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="risk_events")
    trade_decision: Mapped["TradeDecision | None"] = relationship(back_populates="risk_events")


class ExecutionQualitySnapshot(Base):
    __tablename__ = "execution_quality_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    order_event_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("order_events.id", ondelete="SET NULL"), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    broker: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    route_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expected_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    submitted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    liquidity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload_json: Mapped[dict] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="execution_quality_snapshots")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="execution_quality_snapshots")
    order_event: Mapped["OrderEventRecord | None"] = relationship(back_populates="execution_quality_snapshots")


class AuditExport(Base):
    __tablename__ = "audit_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    date_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    export_type: Mapped[str] = mapped_column(String(32), default="audit_bundle")
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="audit_exports")
    requested_by_user: Mapped["User | None"] = relationship(back_populates="requested_audit_exports", foreign_keys=[requested_by_user_id])


class EntitlementUsage(Base):
    __tablename__ = "entitlement_usage"
    __table_args__ = (UniqueConstraint("tenant_id", "period_key", "metric_key", name="uq_entitlement_usage_metric"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    period_key: Mapped[str] = mapped_column(String(32), index=True)
    metric_key: Mapped[str] = mapped_column(String(64), index=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    limit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="entitlement_usage")


class LiveTradingAuthorization(Base):
    __tablename__ = "live_trading_authorizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True)
    linked_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("brokerage_linked_accounts.id", ondelete="CASCADE"), index=True)
    authorization_type: Mapped[str] = mapped_column(String(32), default="supervised_live")
    authorized_mode: Mapped[str] = mapped_column(String(32), default="approval_required")
    max_capital_allocation: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    max_daily_loss: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    max_order_notional: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    allowed_symbols_json: Mapped[dict] = mapped_column("allowed_symbols", JSON, default=list)
    allowed_instruments_json: Mapped[dict] = mapped_column("allowed_instruments", JSON, default=list)
    risk_acknowledgement_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_signature", index=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="live_trading_authorizations")
    user: Mapped["User"] = relationship(back_populates="live_trading_authorizations", foreign_keys=[user_id])
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="live_trading_authorizations")
    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="live_trading_authorizations")
    linked_account: Mapped["BrokerageLinkedAccount"] = relationship(back_populates="live_trading_authorizations")
    live_trading_sessions: Mapped[list["LiveTradingSession"]] = relationship(back_populates="authorization")


class LiveTradingSession(Base):
    __tablename__ = "live_trading_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id", ondelete="CASCADE"), index=True)
    linked_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("brokerage_linked_accounts.id", ondelete="CASCADE"), index=True)
    authorization_id: Mapped[str] = mapped_column(String(36), ForeignKey("live_trading_authorizations.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="armed", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    killed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    max_drawdown: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_order_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="live_trading_sessions")
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="live_trading_sessions")
    strategy_version: Mapped["StrategyVersion"] = relationship(back_populates="live_trading_sessions")
    linked_account: Mapped["BrokerageLinkedAccount"] = relationship(back_populates="live_trading_sessions")
    authorization: Mapped["LiveTradingAuthorization"] = relationship(back_populates="live_trading_sessions")
    order_intents: Mapped[list["LiveOrderIntent"]] = relationship(back_populates="live_trading_session", cascade="all, delete-orphan")
    kill_switch_events: Mapped[list["LiveKillSwitchEvent"]] = relationship(back_populates="live_trading_session")


class LiveOrderIntent(Base):
    __tablename__ = "live_order_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    live_trading_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("live_trading_sessions.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    trade_decision_id: Mapped[str] = mapped_column(String(36), ForeignKey("trade_decisions.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    instrument_type: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    order_type: Mapped[str] = mapped_column(String(16), default="market")
    limit_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    time_in_force: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notional_value: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending_approval", index=True)
    requires_user_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="live_order_intents")
    live_trading_session: Mapped["LiveTradingSession"] = relationship(back_populates="order_intents")
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="live_order_intents")
    trade_decision: Mapped["TradeDecision"] = relationship(back_populates="live_order_intents")
    approved_by_user: Mapped["User | None"] = relationship(back_populates="approved_live_order_intents", foreign_keys=[approved_by_user_id])
    risk_checks: Mapped[list["LiveRiskCheck"]] = relationship(back_populates="live_order_intent", cascade="all, delete-orphan")
    broker_execution_receipts: Mapped[list["BrokerExecutionReceipt"]] = relationship(back_populates="live_order_intent", cascade="all, delete-orphan")


class LiveRiskCheck(Base):
    __tablename__ = "live_risk_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    live_order_intent_id: Mapped[str] = mapped_column(String(36), ForeignKey("live_order_intents.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="CASCADE"), index=True)
    risk_policy_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("risk_policies.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="blocked", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    checks_json: Mapped[dict] = mapped_column("checks", JSON, default=dict)
    blockers_json: Mapped[dict] = mapped_column("blockers", JSON, default=list)
    warnings_json: Mapped[dict] = mapped_column("warnings", JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="live_risk_checks")
    live_order_intent: Mapped["LiveOrderIntent"] = relationship(back_populates="risk_checks")
    strategy_desk: Mapped["StrategyDesk"] = relationship(back_populates="live_risk_checks")
    risk_policy: Mapped["RiskPolicy | None"] = relationship(back_populates="live_risk_checks")


class BrokerExecutionReceipt(Base):
    __tablename__ = "broker_execution_receipts"
    __table_args__ = (UniqueConstraint("broker", "broker_order_id", name="uq_broker_execution_receipt_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    live_order_intent_id: Mapped[str] = mapped_column(String(36), ForeignKey("live_order_intents.id", ondelete="CASCADE"), index=True)
    broker: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    broker_account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="not_submitted", index=True)
    submitted_payload_json: Mapped[dict] = mapped_column("submitted_payload", JSON, default=dict)
    response_payload_json: Mapped[dict] = mapped_column("response_payload", JSON, default=dict)
    filled_quantity: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    average_fill_price: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    fees: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="broker_execution_receipts")
    live_order_intent: Mapped["LiveOrderIntent"] = relationship(back_populates="broker_execution_receipts")


class LiveKillSwitchEvent(Base):
    __tablename__ = "live_kill_switch_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    strategy_desk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True, index=True)
    live_trading_session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("live_trading_sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(24), default="tenant", index=True)
    reason: Mapped[str] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(24), default="user")
    triggered_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    cleared_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped["Tenant"] = relationship(back_populates="live_kill_switch_events")
    strategy_desk: Mapped["StrategyDesk | None"] = relationship(back_populates="live_kill_switch_events")
    live_trading_session: Mapped["LiveTradingSession | None"] = relationship(back_populates="kill_switch_events")
    triggered_by_user: Mapped["User | None"] = relationship(back_populates="triggered_live_kill_switch_events", foreign_keys=[triggered_by_user_id])
    cleared_by_user: Mapped["User | None"] = relationship(back_populates="cleared_live_kill_switch_events", foreign_keys=[cleared_by_user_id])
