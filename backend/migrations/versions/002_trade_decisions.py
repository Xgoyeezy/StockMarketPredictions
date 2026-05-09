from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002_trade_decisions"
down_revision = "001_strategy_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_desk_id", sa.String(36), sa.ForeignKey("strategy_desks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("strategy_run_id", sa.String(36), sa.ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trade_approval_intent_id", sa.String(36), sa.ForeignKey("trade_approval_intents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("order_event_id", sa.String(36), sa.ForeignKey("order_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("instrument_type", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("decision_status", sa.String(32), nullable=False, server_default="recorded"),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("signal_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("risk_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("readiness_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("market_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("broker_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("decision_hash", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "decision_replay_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_decision_id", sa.String(36), sa.ForeignKey("trade_decisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("trade_decision_id", "sequence_number", name="uq_decision_replay_seq"),
    )


def downgrade() -> None:
    op.drop_table("decision_replay_events")
    op.drop_table("trade_decisions")
