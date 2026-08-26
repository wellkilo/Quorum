"""Typed boundaries for messages, extracted commitments, and ledger state."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


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


class Reversibility(StrEnum):
    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


class ImpactRadius(StrEnum):
    INDIVIDUAL = "individual"
    GROUP = "group"
    EXTERNAL = "external"


class MoneyImpact(StrEnum):
    NONE = "none"
    BUDGETED = "budgeted"
    UNBUDGETED = "unbudgeted"


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AutonomyLevel(IntEnum):
    ASK_FIRST = 0
    SUGGEST = 1
    NOTIFY_AND_UNDO = 2
    AUTO_EXECUTE = 3


class TimeoutDefault(StrEnum):
    EXECUTE_AND_NOTIFY = "execute_and_notify"
    EXPIRE_WITHOUT_ACTION = "expire_without_action"


class DecisionStatus(StrEnum):
    AUTHORIZED = "authorized"
    AWAITING_APPROVAL = "awaiting_approval"
    DEFERRED_BUDGET = "deferred_budget"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    UNDONE = "undone"


class ExecutionProvider(StrEnum):
    GOOGLE_CALENDAR = "google_calendar"
    GMAIL = "gmail"
    GOOGLE_FORMS = "google_forms"


class ExecutionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    EXECUTED = "executed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    UNDOING = "undoing"
    UNDONE = "undone"
    UNDO_FAILED = "undo_failed"


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


class ActionRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    action_id: OpaqueId
    organization_id: OpaqueId
    requested_by_id: OpaqueId
    action_class: TaskClass
    tool_name: Annotated[str, Field(min_length=3, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")]
    summary: Annotated[str, Field(min_length=3, max_length=300)]
    reversibility: Reversibility
    impact_radius: ImpactRadius
    money_impact: MoneyImpact
    candidate_decider_ids: Annotated[list[OpaqueId], Field(min_length=1, max_length=10)]
    action_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def require_requested_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        return value

    @field_validator("candidate_decider_ids")
    @classmethod
    def require_unique_deciders(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("candidate_decider_ids must be unique and ordered")
        return value


class RiskAssessment(StrictModel):
    score: int = Field(ge=0, le=9)
    tier: RiskTier
    reversibility_points: int = Field(ge=0, le=3)
    impact_radius_points: int = Field(ge=0, le=3)
    money_impact_points: int = Field(ge=0, le=3)
    reasons: Annotated[list[str], Field(min_length=3, max_length=3)]


class AutonomySnapshot(StrictModel):
    level: AutonomyLevel = AutonomyLevel.ASK_FIRST
    consecutive_approvals: int = Field(default=0, ge=0)
    rejection_count: int = Field(default=0, ge=0)
    undo_count: int = Field(default=0, ge=0)


class InterruptBudgetSnapshot(StrictModel):
    participant_id: OpaqueId
    spent: int = Field(ge=0)
    limit: int = Field(default=2, ge=1)

    @property
    def remaining(self) -> int:
        return max(self.limit - self.spent, 0)


class PolicyDecision(StrictModel):
    action_id: OpaqueId
    organization_id: OpaqueId
    requested_by_id: OpaqueId
    action_class: TaskClass
    tool_name: str
    arguments_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    risk: RiskAssessment
    autonomy: AutonomySnapshot
    required_quorum: int = Field(ge=0, le=10)
    selected_decider_ids: list[OpaqueId] = Field(default_factory=list, max_length=10)
    budgets: list[InterruptBudgetSnapshot] = Field(default_factory=list, max_length=10)
    status: DecisionStatus
    timeout_at: datetime | None = None
    timeout_default: TimeoutDefault

    @model_validator(mode="after")
    def validate_route(self) -> PolicyDecision:
        if self.status is DecisionStatus.AWAITING_APPROVAL:
            if self.required_quorum < 1 or len(self.selected_decider_ids) < self.required_quorum:
                raise ValueError("awaiting approval requires enough selected deciders")
            if self.timeout_at is None:
                raise ValueError("awaiting approval requires timeout_at")
        if self.status is DecisionStatus.AUTHORIZED and self.required_quorum != 0:
            raise ValueError("authorized decisions cannot require quorum")
        return self


class ParticipantResponse(StrictModel):
    participant_id: OpaqueId
    decision: Literal["approve", "reject"]


class InterruptResolution(StrictModel):
    action_id: OpaqueId
    responses: Annotated[list[ParticipantResponse], Field(min_length=1, max_length=10)]

    @field_validator("responses")
    @classmethod
    def require_unique_responses(
        cls, value: list[ParticipantResponse]
    ) -> list[ParticipantResponse]:
        ids = [response.participant_id for response in value]
        if len(ids) != len(set(ids)):
            raise ValueError("each participant may respond once")
        return value


class CalendarActionInput(StrictModel):
    organization_id: OpaqueId
    action_id: OpaqueId
    title: Annotated[str, Field(min_length=1, max_length=300)]
    starts_at: datetime
    ends_at: datetime
    time_zone: Annotated[str, Field(min_length=1, max_length=100)] = "UTC"
    receipt_channel_id: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> CalendarActionInput:
        if self.starts_at.tzinfo is None or self.starts_at.utcoffset() is None:
            raise ValueError("starts_at must include a timezone")
        if self.ends_at.tzinfo is None or self.ends_at.utcoffset() is None:
            raise ValueError("ends_at must include a timezone")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class EmailDraftActionInput(StrictModel):
    organization_id: OpaqueId
    action_id: OpaqueId
    recipient: Annotated[str, Field(min_length=3, max_length=320)]
    subject: Annotated[str, Field(min_length=1, max_length=300)]
    body_text: Annotated[str, Field(min_length=1, max_length=10_000)]
    receipt_channel_id: OpaqueId | None = None

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        local, separator, domain = value.rpartition("@")
        if not separator or not local or "." not in domain or any(char.isspace() for char in value):
            raise ValueError("recipient must be a valid email address")
        return value


class FormQuestion(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    required: bool = False


class FormActionInput(StrictModel):
    organization_id: OpaqueId
    action_id: OpaqueId
    title: Annotated[str, Field(min_length=1, max_length=300)]
    questions: Annotated[list[FormQuestion], Field(min_length=1, max_length=20)]
    receipt_channel_id: OpaqueId | None = None


class ExecutionReceipt(StrictModel):
    organization_id: OpaqueId
    action_id: OpaqueId
    tool_name: str
    provider: ExecutionProvider
    external_resource_id: Annotated[str, Field(min_length=1, max_length=500)]
    external_url: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    status: ExecutionStatus
    reversible: bool
    executed_at: datetime
    undo_expires_at: datetime | None = None
    undo_url: Annotated[str, Field(min_length=1, max_length=3000)] | None = None


class UndoResult(StrictModel):
    organization_id: OpaqueId
    action_id: OpaqueId
    status: ExecutionStatus
    undone_at: datetime
