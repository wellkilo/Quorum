"""Deterministic evidence validation and in-memory commitment ledger."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

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


def _commitment_id(event: CanonicalMessageEvent, candidate: CommitmentCandidate) -> str:
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


class InMemoryLedger:
    """A deterministic ledger implementation for focused unit tests."""

    def __init__(self, items: Iterable[LedgerItem] = ()) -> None:
        self._items = {item.commitment_id: item for item in items}

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

        for index, candidate in enumerate(extraction.commitments):
            evidence_code = evidence_rejection_code(event, candidate)
            if evidence_code is not None:
                changes.rejected.append(
                    RejectedCandidate(candidate_index=index, code=evidence_code)
                )
                continue

            if candidate.operation is CommitmentOperation.CREATE:
                item = LedgerItem(
                    commitment_id=_commitment_id(event, candidate),
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

            refs = list(
                dict.fromkeys([*target.source_message_refs, event.source.source_message_ref])
            )
            if candidate.operation is CommitmentOperation.CANCEL:
                updated = target.model_copy(
                    update={
                        "status": CommitmentStatus.CANCELLED,
                        "source_message_refs": refs,
                        "updated_at": applied_at,
                        "confidence": candidate.confidence,
                    }
                )
            else:
                updated = target.model_copy(
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
            self._items[updated.commitment_id] = updated
            changes.upserted.append(updated)

        return changes


class SQLiteLedger:
    """Local durable ledger with transactional writes and no raw-message storage."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS commitments (
                commitment_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(self, commitment_id: str) -> LedgerItem | None:
        row = self._connection.execute(
            "SELECT payload_json FROM commitments WHERE commitment_id = ?",
            (commitment_id,),
        ).fetchone()
        if row is None:
            return None
        return LedgerItem.model_validate_json(row["payload_json"])

    def values(self) -> tuple[LedgerItem, ...]:
        rows = self._connection.execute(
            "SELECT payload_json FROM commitments ORDER BY commitment_id"
        ).fetchall()
        return tuple(LedgerItem.model_validate_json(row["payload_json"]) for row in rows)

    def apply(
        self,
        event: CanonicalMessageEvent,
        extraction: ExtractionEnvelope,
        *,
        now: datetime | None = None,
    ) -> LedgerChangeSet:
        staged = InMemoryLedger(self.values())
        changes = staged.apply(event, extraction, now=now)
        with self._connection:
            for item in changes.upserted:
                self._connection.execute(
                    """
                    INSERT INTO commitments (
                        commitment_id, organization_id, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(commitment_id) DO UPDATE SET
                        organization_id = excluded.organization_id,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item.commitment_id,
                        item.organization_id,
                        item.model_dump_json(),
                        item.updated_at.isoformat(),
                    ),
                )
        return changes
