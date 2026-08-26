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
