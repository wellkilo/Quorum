"""Add deterministic decision policy and interrupt-budget persistence.

Revision ID: 20260826_0002
Revises: 20260826_0001
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0002"
down_revision: str | Sequence[str] | None = "20260826_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "autonomy_profiles",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("action_class", sa.String(length=40), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("consecutive_approvals", sa.Integer(), nullable=False),
        sa.Column("rejection_count", sa.Integer(), nullable=False),
        sa.Column("undo_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("level >= 0 AND level <= 3", name="ck_autonomy_profile_level"),
        sa.CheckConstraint("consecutive_approvals >= 0", name="ck_autonomy_profile_approvals"),
        sa.CheckConstraint("rejection_count >= 0", name="ck_autonomy_profile_rejections"),
        sa.CheckConstraint("undo_count >= 0", name="ck_autonomy_profile_undos"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.organization_id"]),
        sa.PrimaryKeyConstraint("organization_id", "action_class"),
    )
    op.create_table(
        "interrupt_budget_accounts",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("participant_id", sa.String(length=200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.organization_id"]),
        sa.PrimaryKeyConstraint("organization_id", "participant_id"),
    )
    op.create_table(
        "action_decisions",
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("action_id", sa.String(length=200), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("requested_by_id", sa.String(length=200), nullable=False),
        sa.Column("action_class", sa.String(length=40), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_tier", sa.String(length=20), nullable=False),
        sa.Column("risk_payload", JSON_DOCUMENT, nullable=False),
        sa.Column("autonomy_payload", JSON_DOCUMENT, nullable=False),
        sa.Column("required_quorum", sa.Integer(), nullable=False),
        sa.Column("selected_decider_ids", JSON_DOCUMENT, nullable=False),
        sa.Column("budget_payload", JSON_DOCUMENT, nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_default", sa.String(length=30), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 9", name="ck_action_risk_score"),
        sa.CheckConstraint("risk_tier IN ('low', 'medium', 'high')", name="ck_action_risk_tier"),
        sa.CheckConstraint(
            "status IN ('authorized', 'awaiting_approval', 'deferred_budget', "
            "'approved', 'rejected', 'expired', 'executed', 'undone')",
            name="ck_action_decision_status",
        ),
        sa.CheckConstraint("required_quorum >= 0", name="ck_action_required_quorum"),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_class"],
            ["autonomy_profiles.organization_id", "autonomy_profiles.action_class"],
            name="fk_action_decision_autonomy_profile",
        ),
        sa.PrimaryKeyConstraint("organization_id", "action_id"),
    )
    op.create_index(
        "ix_action_decisions_org_status_timeout",
        "action_decisions",
        ["organization_id", "status", "timeout_at"],
    )
    op.create_table(
        "interrupt_events",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("organization_id", sa.String(length=200), nullable=False),
        sa.Column("action_id", sa.String(length=200), nullable=False),
        sa.Column("participant_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('requested', 'approved', 'rejected', 'expired')",
            name="ck_interrupt_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["action_decisions.organization_id", "action_decisions.action_id"],
            name="fk_interrupt_event_action",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "organization_id",
            "action_id",
            "participant_id",
            "event_type",
            name="uq_interrupt_event_transition",
        ),
    )
    op.create_index(
        "ix_interrupt_events_budget_window",
        "interrupt_events",
        ["organization_id", "participant_id", "event_type", "occurred_at"],
    )
    _create_interrupt_append_only_guards()


def _create_interrupt_append_only_guards() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION quorum_reject_interrupt_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'interrupt_events is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER interrupt_events_append_only
            BEFORE UPDATE OR DELETE ON interrupt_events
            FOR EACH ROW EXECUTE FUNCTION quorum_reject_interrupt_event_mutation()
            """
        )
    elif dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER interrupt_events_no_{operation.lower()}
                BEFORE {operation} ON interrupt_events
                BEGIN
                    SELECT RAISE(ABORT, 'interrupt_events is append-only');
                END
                """
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS interrupt_events_append_only ON interrupt_events")
        op.execute("DROP FUNCTION IF EXISTS quorum_reject_interrupt_event_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS interrupt_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS interrupt_events_no_delete")
    op.drop_index("ix_interrupt_events_budget_window", table_name="interrupt_events")
    op.drop_table("interrupt_events")
    op.drop_index("ix_action_decisions_org_status_timeout", table_name="action_decisions")
    op.drop_table("action_decisions")
    op.drop_table("interrupt_budget_accounts")
    op.drop_table("autonomy_profiles")
