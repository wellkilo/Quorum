"""Typed boundaries for messages, extracted commitments, and ledger state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown fields at every trusted boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DataClassification(StrEnum):
    SYNTHETIC = "synthetic"
    REDACTED_REAL = "redacted-real"


class TaskClass(StrEnum):
    """The six task classes frozen for the hackathon scope."""

    ITEM_HANDOFF = "item_handoff"
    RESOURCE_RESERVATION = "resource_reservation"
    PURCHASE = "purchase"
    INFORMATION_SUBMISSION = "information_submission"
    EXTERNAL_COMMUNICATION = "external_communication"
    EVENT_DECISION = "event_decision"


class CommitmentOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    CANCEL = "cancel"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


OpaqueId = Annotated[str, Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9:_\-.]+$")]
SourceMessageRef = Annotated[
    str, Field(min_length=8, max_length=300, pattern=r"^[a-z0-9_-]+:[^\s:]+:[^\s]+$")
]


class MessageSource(StrictModel):
    provider: Literal["slack"]
    workspace_id: OpaqueId
    source_message_ref: SourceMessageRef


class CanonicalMessageEvent(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    organization_id: OpaqueId
    channel_id: OpaqueId
    message_id: OpaqueId
    actor_id: OpaqueId
    occurred_at: datetime
    text: Annotated[str, Field(min_length=1, max_length=8000)]
    data_classification: DataClassification
    source: MessageSource

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class SourceEvidence(StrictModel):
    source_message_ref: SourceMessageRef
    evidence_quote: Annotated[str, Field(min_length=1, max_length=1000)]


class CommitmentCandidate(StrictModel):
    operation: CommitmentOperation
    task_class: TaskClass
    summary: Annotated[str, Field(min_length=3, max_length=300)]
    owner_id: OpaqueId | None = None
    due_at: datetime | None = None
    target_commitment_id: OpaqueId | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence: SourceEvidence

    @field_validator("due_at")
    @classmethod
    def require_due_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("due_at must include a timezone")
        return value

    @model_validator(mode="after")
    def require_target_for_mutation(self) -> CommitmentCandidate:
        if (
            self.operation in {CommitmentOperation.UPDATE, CommitmentOperation.CANCEL}
            and self.target_commitment_id is None
        ):
            raise ValueError("update and cancel operations require target_commitment_id")
        if self.operation is CommitmentOperation.CREATE and self.target_commitment_id is not None:
            raise ValueError("create operations cannot set target_commitment_id")
        return self


class Ambiguity(StrictModel):
    field: Literal["intent", "owner_id", "due_at", "target_commitment_id"]
    reason: Annotated[str, Field(min_length=3, max_length=300)]
    source_message_ref: SourceMessageRef


class ExtractionEnvelope(StrictModel):
    commitments: list[CommitmentCandidate] = Field(default_factory=list, max_length=10)
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=10)


class ListenerDecision(StrictModel):
    eligible_for_ledger: bool
    reason_code: Literal[
        "explicit_commitment",
        "commitment_mutation",
        "no_commitment",
        "ambiguous",
    ]
    source_message_ref: SourceMessageRef


class LedgerItem(StrictModel):
    commitment_id: OpaqueId
    organization_id: OpaqueId
    task_class: TaskClass
    summary: Annotated[str, Field(min_length=3, max_length=300)]
    owner_id: OpaqueId | None = None
    due_at: datetime | None = None
    status: CommitmentStatus
    source_message_refs: Annotated[list[SourceMessageRef], Field(min_length=1)]
    created_at: datetime
    updated_at: datetime
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class RejectedCandidate(StrictModel):
    candidate_index: int = Field(ge=0)
    code: Literal[
        "SOURCE_REF_MISMATCH",
        "EVIDENCE_NOT_IN_MESSAGE",
        "OWNER_NOT_GROUNDED",
        "TARGET_NOT_FOUND",
        "TARGET_REF_NOT_IN_MESSAGE",
    ]


class LedgerChangeSet(StrictModel):
    upserted: list[LedgerItem] = Field(default_factory=list)
    rejected: list[RejectedCandidate] = Field(default_factory=list)
    duplicate_event: bool = False
