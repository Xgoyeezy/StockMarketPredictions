from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "007_live_trading_core"
down_revision = "006_audit_exports_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_trading_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_version_id", sa.String(36), sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linked_account_id", sa.String(36), sa.ForeignKey("brokerage_linked_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("authorization_type", sa.String(32), nullable=False, server_default="supervised_live"),
        sa.Column("authorized_mode", sa.String(32), nullable=False, server_default="approval_required"),
        sa.Column("max_capital_allocation", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("max_daily_loss", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("max_order_notional", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("allowed_symbols", sa.JSON(), nullable=False),
        sa.Column("allowed_instruments", sa.JSON(), nullable=False),
        sa.Column("risk_acknowledgement_version", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_signature"),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "live_trading_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_version_id", sa.String(36), sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linked_account_id", sa.String(36), sa.ForeignKey("brokerage_linked_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("authorization_id", sa.String(36), sa.ForeignKey("live_trading_authorizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="armed"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("killed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("max_drawdown", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "live_order_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("live_trading_session_id", sa.String(36), sa.ForeignKey("live_trading_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_decision_id", sa.String(36), sa.ForeignKey("trade_decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("instrument_type", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("order_type", sa.String(16), nullable=False, server_default="market"),
        sa.Column("limit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("stop_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("time_in_force", sa.String(16), nullable=True),
        sa.Column("notional_value", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_approval"),
        sa.Column("requires_user_approval", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("approved_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("duplicate_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "live_risk_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("live_order_intent_id", sa.String(36), sa.ForeignKey("live_order_intents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("risk_policy_id", sa.String(36), sa.ForeignKey("risk_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="blocked"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("live_risk_checks")
    op.drop_table("live_order_intents")
    op.drop_table("live_trading_sessions")
    op.drop_table("live_trading_authorizations")
