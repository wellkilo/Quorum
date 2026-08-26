"""Deterministic evidence validation and in-memory commitment ledger."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol

from quorum.models import (
    CanonicalMessageEvent,
    CommitmentCandidate,
    CommitmentOperation,
    CommitmentStatus,
    ExtractionEnvelope,
    LedgerChangeSet,
    LedgerItem,
    RejectedCandidate,
)


def commitment_id_for(event: CanonicalMessageEvent, candidate: CommitmentCandidate) -> str:
    material = "|".join(
        (
            event.organization_id,
            event.source.source_message_ref,
            candidate.operation.value,
            candidate.task_class.value,
            candidate.summary.casefold(),
            candidate.owner_id or "",
        )
    )
    return f"cmt_{sha256(material.encode('utf-8')).hexdigest()[:20]}"


EvidenceRejectionCode = Literal[
    "SOURCE_REF_MISMATCH",
    "EVIDENCE_NOT_IN_MESSAGE",
    "OWNER_NOT_GROUNDED",
    "TARGET_REF_NOT_IN_MESSAGE",
]


class LedgerRepository(Protocol):
    """Storage boundary shared by local and production ledger backends."""

    def apply(
        self,
        event: CanonicalMessageEvent,
        extraction: ExtractionEnvelope,
        *,
        now: datetime | None = None,
    ) -> LedgerChangeSet: ...


def evidence_rejection_code(
    event: CanonicalMessageEvent, candidate: CommitmentCandidate
) -> EvidenceRejectionCode | None:
    """Return a stable rejection code when model evidence is not grounded."""

    if candidate.evidence.source_message_ref != event.source.source_message_ref:
        return "SOURCE_REF_MISMATCH"
    if candidate.evidence.evidence_quote not in event.text:
        return "EVIDENCE_NOT_IN_MESSAGE"
    if (
        candidate.owner_id is not None
        and candidate.owner_id != event.actor_id
        and f"<@{candidate.owner_id}>" not in event.text
    ):
        return "OWNER_NOT_GROUNDED"
    if (
        candidate.target_commitment_id is not None
        and candidate.target_commitment_id not in event.text
    ):
        return "TARGET_REF_NOT_IN_MESSAGE"
    return None


def apply_candidate(
    event: CanonicalMessageEvent,
    candidate: CommitmentCandidate,
    *,
    target: LedgerItem | None,
    applied_at: datetime,
) -> LedgerItem:
    """Apply one validated candidate without performing storage I/O."""

    if candidate.operation is CommitmentOperation.CREATE:
        return LedgerItem(
            commitment_id=commitment_id_for(event, candidate),
            organization_id=event.organization_id,
            task_class=candidate.task_class,
            summary=candidate.summary,
            owner_id=candidate.owner_id,
            due_at=candidate.due_at,
            status=CommitmentStatus.OPEN,
            source_message_refs=[event.source.source_message_ref],
            created_at=applied_at,
            updated_at=applied_at,
            confidence=candidate.confidence,
        )
    if target is None:
        raise ValueError("target is required for update and cancel operations")

    refs = list(dict.fromkeys([*target.source_message_refs, event.source.source_message_ref]))
    if candidate.operation is CommitmentOperation.CANCEL:
        return target.model_copy(
            update={
                "status": CommitmentStatus.CANCELLED,
                "source_message_refs": refs,
                "updated_at": applied_at,
                "confidence": candidate.confidence,
            }
        )
    return target.model_copy(
        update={
            "task_class": candidate.task_class,
            "summary": candidate.summary,
            "owner_id": candidate.owner_id,
            "due_at": candidate.due_at,
            "source_message_refs": refs,
            "updated_at": applied_at,
            "confidence": candidate.confidence,
        }
    )


class InMemoryLedger:
    """A deterministic ledger implementation for focused unit tests."""

    def __init__(self, items: Iterable[LedgerItem] = ()) -> None:
        self._items = {item.commitment_id: item for item in items}
        self._processed_messages: set[tuple[str, str]] = set()

    def get(self, commitment_id: str) -> LedgerItem | None:
        return self._items.get(commitment_id)

    def values(self) -> tuple[LedgerItem, ...]:
        return tuple(self._items.values())

    def apply(
        self,
        event: CanonicalMessageEvent,
        extraction: ExtractionEnvelope,
        *,
        now: datetime | None = None,
    ) -> LedgerChangeSet:
        applied_at = now or datetime.now(UTC)
        changes = LedgerChangeSet()
        message_key = (event.organization_id, event.source.source_message_ref)
        if message_key in self._processed_messages:
            return LedgerChangeSet(duplicate_event=True)
        self._processed_messages.add(message_key)

        for index, candidate in enumerate(extraction.commitments):
            evidence_code = evidence_rejection_code(event, candidate)
            if evidence_code is not None:
                changes.rejected.append(
                    RejectedCandidate(candidate_index=index, code=evidence_code)
                )
                continue

            if candidate.operation is CommitmentOperation.CREATE:
                item = apply_candidate(event, candidate, target=None, applied_at=applied_at)
                self._items[item.commitment_id] = item
                changes.upserted.append(item)
                continue

            target_id = candidate.target_commitment_id
            target = self._items.get(target_id or "")
            if target is None or target.organization_id != event.organization_id:
                changes.rejected.append(
                    RejectedCandidate(candidate_index=index, code="TARGET_NOT_FOUND")
                )
                continue

            updated = apply_candidate(event, candidate, target=target, applied_at=applied_at)
            self._items[updated.commitment_id] = updated
            changes.upserted.append(updated)

        return changes
