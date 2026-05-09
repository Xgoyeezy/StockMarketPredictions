from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "008_live_execution_controls"
down_revision = "007_live_trading_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_execution_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("live_order_intent_id", sa.String(36), sa.ForeignKey("live_order_intents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("broker_account_id", sa.String(120), nullable=True),
        sa.Column("broker_order_id", sa.String(120), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="not_submitted"),
        sa.Column("submitted_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("average_fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("fees", sa.Numeric(18, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("broker", "broker_order_id", name="uq_broker_execution_receipt_order"),
    )
    op.create_table(
        "live_kill_switch_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("live_trading_session_id", sa.String(36), sa.ForeignKey("live_trading_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scope", sa.String(24), nullable=False, server_default="tenant"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.String(24), nullable=False, server_default="user"),
        sa.Column("triggered_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("live_kill_switch_events")
    op.drop_table("broker_execution_receipts")
