from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001_strategy_lifecycle"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="internal"),
        sa.Column("source_hash", sa.String(128), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("risk_profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "strategy_desk_id", "version_number", name="uq_strategy_version_number"),
    )
    op.create_table(
        "strategy_deployments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_version_id", sa.String(36), sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linked_account_id", sa.String(36), sa.ForeignKey("brokerage_linked_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mode", sa.String(24), nullable=False, server_default="paper"),
        sa.Column("status", sa.String(32), nullable=False, server_default="stopped"),
        sa.Column("allocation_cap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_order_notional", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_daily_loss", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "strategy_promotion_gates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_stage", sa.String(32), nullable=False),
        sa.Column("to_stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("required_score", sa.Integer(), nullable=False),
        sa.Column("actual_score", sa.Integer(), nullable=True),
        sa.Column("requirements", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("blockers", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("strategy_promotion_gates")
    op.drop_table("strategy_deployments")
    op.drop_table("strategy_versions")
