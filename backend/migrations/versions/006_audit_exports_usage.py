from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006_audit_exports_usage"
down_revision = "005_execution_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_exports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("date_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_type", sa.String(32), nullable=False, server_default="audit_bundle"),
        sa.Column("file_path", sa.String(512), nullable=True),
        sa.Column("checksum", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "entitlement_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_key", sa.String(32), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "period_key", "metric_key", name="uq_entitlement_usage_metric"),
    )


def downgrade() -> None:
    op.drop_table("entitlement_usage")
    op.drop_table("audit_exports")
