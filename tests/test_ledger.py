from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from quorum.ledger import InMemoryLedger, SQLiteLedger
from quorum.models import (
    CanonicalMessageEvent,
    CommitmentCandidate,
    CommitmentOperation,
    CommitmentStatus,
    DataClassification,
    ExtractionEnvelope,
    MessageSource,
    SourceEvidence,
    TaskClass,
)


def make_event(text: str = "I will bring the keys by Friday.") -> CanonicalMessageEvent:
    return CanonicalMessageEvent(
        organization_id="org_test",
        channel_id="channel_test",
        message_id="message_test",
        actor_id="person_test",
        occurred_at=datetime(2026, 8, 26, 10, tzinfo=UTC),
        text=text,
        data_classification=DataClassification.SYNTHETIC,
        source=MessageSource(
            provider="slack",
            workspace_id="workspace_test",
            source_message_ref="slack:C_TEST:1780000000.000100",
        ),
    )


def make_candidate(
    *,
    operation: CommitmentOperation = CommitmentOperation.CREATE,
    source_ref: str = "slack:C_TEST:1780000000.000100",
    quote: str = "I will bring the keys",
    target_id: str | None = None,
) -> CommitmentCandidate:
    return CommitmentCandidate(
        operation=operation,
        task_class=TaskClass.ITEM_HANDOFF,
        summary="Bring the keys",
        owner_id="person_test",
        due_at=datetime(2026, 8, 28, 17, tzinfo=UTC),
        target_commitment_id=target_id,
        confidence=0.95,
        evidence=SourceEvidence(
            source_message_ref=source_ref,
            evidence_quote=quote,
        ),
    )


class CommitmentLedgerTest(unittest.TestCase):
    def test_create_requires_grounded_evidence_and_stores_source(self) -> None:
        event = make_event()
        ledger = InMemoryLedger()

        changes = ledger.apply(
            event,
            ExtractionEnvelope(commitments=[make_candidate()]),
            now=datetime(2026, 8, 26, 10, 1, tzinfo=UTC),
        )

        self.assertEqual(len(changes.upserted), 1)
        self.assertEqual(changes.rejected, [])
        item = changes.upserted[0]
        self.assertEqual(item.source_message_refs, [event.source.source_message_ref])
        self.assertEqual(item.status, CommitmentStatus.OPEN)
        self.assertEqual(ledger.get(item.commitment_id), item)

    def test_mismatched_source_reference_is_rejected(self) -> None:
        changes = InMemoryLedger().apply(
            make_event(),
            ExtractionEnvelope(
                commitments=[make_candidate(source_ref="slack:C_TEST:1780000999.000100")]
            ),
        )

        self.assertEqual(changes.upserted, [])
        self.assertEqual(changes.rejected[0].code, "SOURCE_REF_MISMATCH")

    def test_non_verbatim_evidence_is_rejected(self) -> None:
        changes = InMemoryLedger().apply(
            make_event(),
            ExtractionEnvelope(commitments=[make_candidate(quote="I definitely promised")]),
        )

        self.assertEqual(changes.upserted, [])
        self.assertEqual(changes.rejected[0].code, "EVIDENCE_NOT_IN_MESSAGE")

    def test_owner_without_actor_or_explicit_mention_is_rejected(self) -> None:
        candidate = make_candidate().model_copy(update={"owner_id": "person_other"})

        changes = InMemoryLedger().apply(make_event(), ExtractionEnvelope(commitments=[candidate]))

        self.assertEqual(changes.upserted, [])
        self.assertEqual(changes.rejected[0].code, "OWNER_NOT_GROUNDED")

    def test_update_and_cancel_preserve_evidence_chain(self) -> None:
        ledger = InMemoryLedger()
        created = ledger.apply(make_event(), ExtractionEnvelope(commitments=[make_candidate()]))
        target_id = created.upserted[0].commitment_id
        update_event = make_event(
            f"Update {target_id}: I will bring the keys on Saturday instead."
        ).model_copy(
            update={
                "message_id": "message_update",
                "source": MessageSource(
                    provider="slack",
                    workspace_id="workspace_test",
                    source_message_ref="slack:C_TEST:1780000001.000100",
                ),
            }
        )
        updated_candidate = make_candidate(
            operation=CommitmentOperation.UPDATE,
            source_ref=update_event.source.source_message_ref,
            quote="I will bring the keys on Saturday instead",
            target_id=target_id,
        ).model_copy(update={"summary": "Bring the keys on Saturday"})

        updated = ledger.apply(
            update_event, ExtractionEnvelope(commitments=[updated_candidate])
        ).upserted[0]

        self.assertEqual(updated.summary, "Bring the keys on Saturday")
        self.assertEqual(len(updated.source_message_refs), 2)

    def test_mutation_without_target_is_invalid(self) -> None:
        with self.assertRaises(ValidationError):
            make_candidate(operation=CommitmentOperation.CANCEL)

    def test_mutation_target_must_be_named_in_message(self) -> None:
        event = make_event("Cancel the old key handoff.")
        candidate = make_candidate(
            operation=CommitmentOperation.CANCEL,
            quote="Cancel the old key handoff",
            target_id="cmt_hidden_target",
        )

        changes = InMemoryLedger().apply(event, ExtractionEnvelope(commitments=[candidate]))

        self.assertEqual(changes.upserted, [])
        self.assertEqual(changes.rejected[0].code, "TARGET_REF_NOT_IN_MESSAGE")

    def test_cross_organization_mutation_is_hidden_as_missing(self) -> None:
        ledger = InMemoryLedger()
        created = ledger.apply(make_event(), ExtractionEnvelope(commitments=[make_candidate()]))
        target_id = created.upserted[0].commitment_id
        other_event = make_event(f"Cancel {target_id}: no longer needed.").model_copy(
            update={"organization_id": "org_other"}
        )
        candidate = make_candidate(
            operation=CommitmentOperation.CANCEL,
            quote="no longer needed",
            target_id=target_id,
        )

        changes = ledger.apply(other_event, ExtractionEnvelope(commitments=[candidate]))

        self.assertEqual(changes.upserted, [])
        self.assertEqual(changes.rejected[0].code, "TARGET_NOT_FOUND")

    def test_sqlite_ledger_persists_without_raw_message(self) -> None:
        event = make_event("Private message: I will bring the keys by Friday.")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with SQLiteLedger(path) as ledger:
                created = ledger.apply(
                    event, ExtractionEnvelope(commitments=[make_candidate()])
                ).upserted[0]
            with SQLiteLedger(path) as reopened:
                persisted = reopened.get(created.commitment_id)

            self.assertEqual(persisted, created)
            self.assertNotIn(b"Private message", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
