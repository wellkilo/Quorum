"""SQLAlchemy schema for Quorum's durable business facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class OrganizationRow(Base):
    __tablename__ = "organizations"

    organization_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProcessedMessageRow(Base):
    __tablename__ = "processed_messages"
    __table_args__ = (
        UniqueConstraint("organization_id", "message_id", name="uq_processed_message_external_id"),
        CheckConstraint(
            "data_classification IN ('synthetic', 'redacted-real')",
            name="ck_processed_message_classification",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("organizations.organization_id"), primary_key=True
    )
    source_message_ref: Mapped[str] = mapped_column(String(300), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_classification: Mapped[str] = mapped_column(String(20), nullable=False)


class CommitmentRow(Base):
    __tablename__ = "commitments"
    __table_args__ = (
        CheckConstraint(
            "task_class IN ('item_handoff', 'resource_reservation', 'purchase', "
            "'information_submission', 'external_communication', 'event_decision')",
            name="ck_commitment_task_class",
        ),
        CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')", name="ck_commitment_status"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_commitment_confidence"),
        CheckConstraint("version >= 1", name="ck_commitment_version"),
        Index("ix_commitments_org_status_due", "organization_id", "status", "due_at"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("organizations.organization_id"), primary_key=True
    )
    commitment_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    task_class: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(200))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source_message_refs: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CommitmentEventRow(Base):
    __tablename__ = "commitment_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "commitment_id"],
            ["commitments.organization_id", "commitments.commitment_id"],
            name="fk_commitment_event_commitment",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_message_ref"],
            ["processed_messages.organization_id", "processed_messages.source_message_ref"],
            name="fk_commitment_event_source_message",
        ),
        CheckConstraint(
            "event_type IN ('created', 'updated', 'cancelled')",
            name="ck_commitment_event_type",
        ),
        Index(
            "ix_commitment_events_org_commitment_created",
            "organization_id",
            "commitment_id",
            "created_at",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(200), nullable=False)
    commitment_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_message_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AutonomyProfileRow(Base):
    __tablename__ = "autonomy_profiles"
    __table_args__ = (
        CheckConstraint("level >= 0 AND level <= 3", name="ck_autonomy_profile_level"),
        CheckConstraint("consecutive_approvals >= 0", name="ck_autonomy_profile_approvals"),
        CheckConstraint("rejection_count >= 0", name="ck_autonomy_profile_rejections"),
        CheckConstraint("undo_count >= 0", name="ck_autonomy_profile_undos"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("organizations.organization_id"), primary_key=True
    )
    action_class: Mapped[str] = mapped_column(String(40), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    undo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InterruptBudgetAccountRow(Base):
    __tablename__ = "interrupt_budget_accounts"

    organization_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("organizations.organization_id"), primary_key=True
    )
    participant_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionDecisionRow(Base):
    __tablename__ = "action_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "action_class"],
            ["autonomy_profiles.organization_id", "autonomy_profiles.action_class"],
            name="fk_action_decision_autonomy_profile",
        ),
        CheckConstraint("risk_score >= 0 AND risk_score <= 9", name="ck_action_risk_score"),
        CheckConstraint("risk_tier IN ('low', 'medium', 'high')", name="ck_action_risk_tier"),
        CheckConstraint(
            "status IN ('authorized', 'awaiting_approval', 'deferred_budget', "
            "'approved', 'rejected', 'expired', 'executed', 'undone')",
            name="ck_action_decision_status",
        ),
        CheckConstraint("required_quorum >= 0", name="ck_action_required_quorum"),
        Index(
            "ix_action_decisions_org_status_timeout",
            "organization_id",
            "status",
            "timeout_at",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action_class: Mapped[str] = mapped_column(String(40), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    autonomy_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    required_quorum: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_decider_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    budget_payload: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeout_default: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InterruptEventRow(Base):
    __tablename__ = "interrupt_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "action_id"],
            ["action_decisions.organization_id", "action_decisions.action_id"],
            name="fk_interrupt_event_action",
        ),
        CheckConstraint(
            "event_type IN ('requested', 'approved', 'rejected', 'expired')",
            name="ck_interrupt_event_type",
        ),
        UniqueConstraint(
            "organization_id",
            "action_id",
            "participant_id",
            "event_type",
            name="uq_interrupt_event_transition",
        ),
        Index(
            "ix_interrupt_events_budget_window",
            "organization_id",
            "participant_id",
            "event_type",
            "occurred_at",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action_id: Mapped[str] = mapped_column(String(200), nullable=False)
    participant_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
