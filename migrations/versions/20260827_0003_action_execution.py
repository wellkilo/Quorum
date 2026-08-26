"""Add reversible action execution receipts and one-time undo tokens.

Revision ID: 20260827_0003
Revises: 20260826_0002
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0003"
down_revision: str | Sequence[str] | None = "20260826_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "action_decisions",
        sa.Column(
            "arguments_fingerprint",
            sa.String(length=64),
            nullable=False,
            server_default="44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("action_decisions", "arguments_fingerprint", server_default=None)

    op.create_table(
        "action_executions",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("action_id", sa.String(length=200), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("arguments_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_resource_id", sa.String(length=500), nullable=True),
        sa.Column("external_url", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undo_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("receipt_channel_id", sa.String(length=200), nullable=True),
        sa.Column("receipt_message_ts", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_progress', 'executed', 'failed', 'uncertain', "
            "'undoing', 'undone', 'undo_failed')",
            name="ck_action_execution_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["action_decisions.organization_id", "action_decisions.action_id"],
            name="fk_action_execution_decision",
        ),
        sa.PrimaryKeyConstraint("organization_id", "action_id"),
    )
    op.create_table(
        "undo_tokens",
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("action_id", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["action_executions.organization_id", "action_executions.action_id"],
            name="fk_undo_token_execution",
        ),
        sa.PrimaryKeyConstraint("token_digest"),
    )
    op.create_index(
        "ix_undo_tokens_expiry",
        "undo_tokens",
        ["expires_at", "consumed_at"],
    )
    op.create_table(
        "execution_events",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("action_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_code", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('started', 'executed', 'failed', 'uncertain', "
            "'undo_started', 'undone', 'undo_failed', 'receipt_sent', 'receipt_failed')",
            name="ck_execution_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["action_executions.organization_id", "action_executions.action_id"],
            name="fk_execution_event_execution",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_execution_events_action_time",
        "execution_events",
        ["organization_id", "action_id", "occurred_at"],
    )
    _create_execution_event_guards()


def _create_execution_event_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION quorum_reject_execution_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'execution_events is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER execution_events_append_only
            BEFORE UPDATE OR DELETE ON execution_events
            FOR EACH ROW EXECUTE FUNCTION quorum_reject_execution_event_mutation()
            """
        )
    elif dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER execution_events_no_{operation.lower()}
                BEFORE {operation} ON execution_events
                BEGIN
                    SELECT RAISE(ABORT, 'execution_events is append-only');
                END
                """
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS execution_events_append_only ON execution_events")
        op.execute("DROP FUNCTION IF EXISTS quorum_reject_execution_event_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS execution_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS execution_events_no_delete")
    op.drop_index("ix_execution_events_action_time", table_name="execution_events")
    op.drop_table("execution_events")
    op.drop_index("ix_undo_tokens_expiry", table_name="undo_tokens")
    op.drop_table("undo_tokens")
    op.drop_table("action_executions")
    op.drop_column("action_decisions", "arguments_fingerprint")
