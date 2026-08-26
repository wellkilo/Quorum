"""Create tenant-isolated commitment ledger and append-only audit log.

Revision ID: 20260826_0001
Revises: None
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_table(
        "processed_messages",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("source_message_ref", sa.String(length=300), nullable=False),
        sa.Column("message_id", sa.String(length=200), nullable=False),
        sa.Column("event_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_classification", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "data_classification IN ('synthetic', 'redacted-real')",
            name="ck_processed_message_classification",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.organization_id"]),
        sa.PrimaryKeyConstraint("organization_id", "source_message_ref"),
        sa.UniqueConstraint(
            "organization_id", "message_id", name="uq_processed_message_external_id"
        ),
    )
    op.create_table(
        "commitments",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("commitment_id", sa.String(length=200), nullable=False),
        sa.Column("task_class", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("owner_id", sa.String(length=200), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_message_refs", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "task_class IN ('item_handoff', 'resource_reservation', 'purchase', "
            "'information_submission', 'external_communication', 'event_decision')",
            name="ck_commitment_task_class",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')", name="ck_commitment_status"
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_commitment_confidence"),
        sa.CheckConstraint("version >= 1", name="ck_commitment_version"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.organization_id"]),
        sa.PrimaryKeyConstraint("organization_id", "commitment_id"),
    )
    op.create_index(
        "ix_commitments_org_status_due",
        "commitments",
        ["organization_id", "status", "due_at"],
    )
    op.create_table(
        "commitment_events",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("commitment_id", sa.String(length=200), nullable=False),
        sa.Column("source_message_ref", sa.String(length=300), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("snapshot", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('created', 'updated', 'cancelled')",
            name="ck_commitment_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "commitment_id"],
            ["commitments.organization_id", "commitments.commitment_id"],
            name="fk_commitment_event_commitment",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_message_ref"],
            ["processed_messages.organization_id", "processed_messages.source_message_ref"],
            name="fk_commitment_event_source_message",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_commitment_events_org_commitment_created",
        "commitment_events",
        ["organization_id", "commitment_id", "created_at"],
    )
    _create_append_only_guards()


def _create_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION quorum_reject_commitment_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'commitment_events is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER commitment_events_append_only
            BEFORE UPDATE OR DELETE ON commitment_events
            FOR EACH ROW EXECUTE FUNCTION quorum_reject_commitment_event_mutation()
            """
        )
    elif dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER commitment_events_no_{operation.lower()}
                BEFORE {operation} ON commitment_events
                BEGIN
                    SELECT RAISE(ABORT, 'commitment_events is append-only');
                END
                """
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS commitment_events_append_only ON commitment_events")
        op.execute("DROP FUNCTION IF EXISTS quorum_reject_commitment_event_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS commitment_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS commitment_events_no_delete")
    op.drop_index("ix_commitment_events_org_commitment_created", table_name="commitment_events")
    op.drop_table("commitment_events")
    op.drop_index("ix_commitments_org_status_due", table_name="commitments")
    op.drop_table("commitments")
    op.drop_table("processed_messages")
    op.drop_table("organizations")
