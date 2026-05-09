from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "004_risk_controls"
down_revision = "003_readiness_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("max_daily_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_weekly_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_position_notional", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_order_notional", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_open_positions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allowed_symbols", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("blocked_symbols", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("allowed_instruments", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("requires_approval_above", sa.Float(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trade_decision_id", sa.String(36), sa.ForeignKey("trade_decisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("breached_rule", sa.String(64), nullable=True),
        sa.Column("action_taken", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("risk_events")
    op.drop_table("risk_policies")
