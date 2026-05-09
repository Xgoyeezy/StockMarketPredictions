from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003_readiness_scoring"
down_revision = "002_trade_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "readiness_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("strategy_run_id", sa.String(36), sa.ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("components", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("hard_blockers", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "readiness_blockers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("readiness_snapshot_id", sa.String(36), sa.ForeignKey("readiness_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blocker_key", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("readiness_blockers")
    op.drop_table("readiness_snapshots")
