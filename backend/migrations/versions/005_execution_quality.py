from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005_execution_quality"
down_revision = "004_risk_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_quality_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("order_event_id", sa.String(36), sa.ForeignKey("order_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trade_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("route_state", sa.String(32), nullable=True),
        sa.Column("expected_price", sa.Float(), nullable=True),
        sa.Column("submitted_price", sa.Float(), nullable=True),
        sa.Column("filled_price", sa.Float(), nullable=True),
        sa.Column("spread_bps", sa.Float(), nullable=True),
        sa.Column("slippage_bps", sa.Float(), nullable=True),
        sa.Column("estimated_cost_bps", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("liquidity_score", sa.Float(), nullable=True),
        sa.Column("execution_score", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("execution_quality_snapshots")
