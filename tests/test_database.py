from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from quorum.database import (
    DatabaseConfigurationError,
    DatabaseLedger,
    DatabaseSettings,
    IdempotencyConflictError,
    create_database_engine,
)
from quorum.database_cli import _migration_paths
from quorum.models import (
    CanonicalMessageEvent,
    CommitmentCandidate,
    CommitmentOperation,
    DataClassification,
    ExtractionEnvelope,
    MessageSource,
    SourceEvidence,
    TaskClass,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_event(
    *,
    organization_id: str = "org_test",
    message_id: str = "message_test",
    source_ref: str = "slack:C_TEST:1780000000.000100",
    text_value: str = "I will bring the keys by Friday.",
) -> CanonicalMessageEvent:
    return CanonicalMessageEvent(
        organization_id=organization_id,
        channel_id="channel_test",
        message_id=message_id,
        actor_id="person_test",
        occurred_at=datetime(2026, 8, 26, 10, tzinfo=UTC),
        text=text_value,
        data_classification=DataClassification.SYNTHETIC,
        source=MessageSource(
            provider="slack",
            workspace_id="workspace_test",
            source_message_ref=source_ref,
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


class DatabaseSettingsTest(unittest.TestCase):
    def test_migration_assets_are_discoverable(self) -> None:
        config_path, script_path = _migration_paths()

        self.assertTrue(config_path.is_file())
        self.assertTrue((script_path / "env.py").is_file())
        self.assertTrue((script_path / "versions" / "20260826_0001_commitment_ledger.py").is_file())
        self.assertTrue((script_path / "versions" / "20260826_0002_decision_policy.py").is_file())

    def test_defaults_to_local_sqlite(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = DatabaseSettings.from_environment()

        self.assertEqual(settings.url, "sqlite+pysqlite:///./var/quorum.sqlite3")

    def test_normalizes_postgres_url_to_psycopg3(self) -> None:
        with patch.dict(
            os.environ,
            {"QUORUM_DATABASE_URL": "postgres://user:secret@db.example/quorum"},
            clear=True,
        ):
            settings = DatabaseSettings.from_environment()

        self.assertEqual(settings.url, "postgresql+psycopg://user:secret@db.example/quorum")

    def test_rejects_unapproved_database_driver(self) -> None:
        with (
            patch.dict(os.environ, {"QUORUM_DATABASE_URL": "mysql://localhost/quorum"}, clear=True),
            self.assertRaises(DatabaseConfigurationError),
        ):
            DatabaseSettings.from_environment()

    def test_postgresql_offline_migration_uses_production_types_and_guard(self) -> None:
        output = StringIO()
        config = Config(PROJECT_ROOT / "alembic.ini", output_buffer=output)
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

        with patch.dict(
            os.environ,
            {
                "QUORUM_DATABASE_URL": (
                    "postgresql+psycopg://user:secret@db.example/quorum?sslmode=require"
                )
            },
            clear=True,
        ):
            command.upgrade(config, "head", sql=True)

        ddl = output.getvalue()
        self.assertIn("TIMESTAMP WITH TIME ZONE", ddl)
        self.assertIn("JSONB NOT NULL", ddl)
        self.assertIn("PRIMARY KEY (organization_id, commitment_id)", ddl)
        self.assertIn("quorum_reject_commitment_event_mutation", ddl)
        self.assertIn("BEFORE UPDATE OR DELETE ON commitment_events", ddl)
        self.assertIn("quorum_reject_interrupt_event_mutation", ddl)
        self.assertIn("BEFORE UPDATE OR DELETE ON interrupt_events", ddl)
        self.assertIn("CREATE TABLE autonomy_profiles", ddl)
        self.assertIn("CREATE TABLE action_decisions", ddl)
        self.assertIn("CREATE TABLE interrupt_events", ddl)
        self.assertIn(
            "FOREIGN KEY(organization_id, action_class) "
            "REFERENCES autonomy_profiles (organization_id, action_class)",
            ddl,
        )
        self.assertIn(
            "FOREIGN KEY(organization_id, action_id) "
            "REFERENCES action_decisions (organization_id, action_id)",
            ddl,
        )
        self.assertNotIn("secret", ddl)


class DatabaseLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "ledger.sqlite3"
        self.url = f"sqlite+pysqlite:///{self.path}"
        self.engine = create_database_engine(DatabaseSettings(url=self.url))
        config = Config(PROJECT_ROOT / "alembic.ini")
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        self.ledger = DatabaseLedger(self.engine)

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp_dir.cleanup()

    def test_migration_creates_expected_tables(self) -> None:
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {
                "action_decisions",
                "alembic_version",
                "autonomy_profiles",
                "commitment_events",
                "commitments",
                "interrupt_budget_accounts",
                "interrupt_events",
                "organizations",
                "processed_messages",
            },
        )
        foreign_keys = inspect(self.engine).get_foreign_keys("commitment_events")
        self.assertEqual(
            {key["name"] for key in foreign_keys},
            {"fk_commitment_event_commitment", "fk_commitment_event_source_message"},
        )

    def test_migration_round_trip_recreates_schema(self) -> None:
        config = Config(PROJECT_ROOT / "alembic.ini")
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
        self.assertEqual(set(inspect(self.engine).get_table_names()), {"alembic_version"})

        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        self.assertEqual(
            set(inspect(self.engine).get_table_names()),
            {
                "action_decisions",
                "alembic_version",
                "autonomy_profiles",
                "commitment_events",
                "commitments",
                "interrupt_budget_accounts",
                "interrupt_events",
                "organizations",
                "processed_messages",
            },
        )

    def test_create_persists_business_fact_and_append_only_event(self) -> None:
        event = make_event(text_value="Private message: I will bring the keys by Friday.")

        created = self.ledger.apply(
            event, ExtractionEnvelope(commitments=[make_candidate()])
        ).upserted[0]

        self.assertEqual(self.ledger.get(event.organization_id, created.commitment_id), created)
        audit = self.ledger.audit_events(event.organization_id)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["event_type"], "created")
        self.assertNotIn(b"Private message", self.path.read_bytes())

    def test_exact_retry_is_idempotent(self) -> None:
        event = make_event()
        extraction = ExtractionEnvelope(commitments=[make_candidate()])

        first = self.ledger.apply(event, extraction)
        second = self.ledger.apply(event, extraction)

        self.assertEqual(len(first.upserted), 1)
        self.assertTrue(second.duplicate_event)
        self.assertEqual(len(self.ledger.values(event.organization_id)), 1)
        self.assertEqual(len(self.ledger.audit_events(event.organization_id)), 1)

    def test_same_identity_with_changed_content_is_rejected(self) -> None:
        event = make_event()
        self.ledger.apply(event, ExtractionEnvelope(commitments=[make_candidate()]))
        changed = event.model_copy(update={"text": "Changed payload with the same identity."})

        with self.assertRaises(IdempotencyConflictError):
            self.ledger.apply(changed, ExtractionEnvelope())

    def test_failed_transaction_rolls_back_message_claim_and_all_rows(self) -> None:
        event = make_event()
        duplicate_candidate = make_candidate()

        with self.assertRaises(IntegrityError):
            self.ledger.apply(
                event,
                ExtractionEnvelope(commitments=[duplicate_candidate, duplicate_candidate]),
            )

        self.assertEqual(self.ledger.values(event.organization_id), ())
        self.assertEqual(self.ledger.audit_events(event.organization_id), ())
        retry = self.ledger.apply(event, ExtractionEnvelope(commitments=[duplicate_candidate]))
        self.assertEqual(len(retry.upserted), 1)
        self.assertFalse(retry.duplicate_event)

    def test_tenant_isolation_hides_commitments(self) -> None:
        event = make_event()
        created = self.ledger.apply(
            event, ExtractionEnvelope(commitments=[make_candidate()])
        ).upserted[0]

        self.assertIsNone(self.ledger.get("org_other", created.commitment_id))
        self.assertEqual(self.ledger.values("org_other"), ())

    def test_update_adds_event_and_increments_version(self) -> None:
        event = make_event()
        created = self.ledger.apply(
            event, ExtractionEnvelope(commitments=[make_candidate()])
        ).upserted[0]
        update_ref = "slack:C_TEST:1780000001.000100"
        update_event = make_event(
            message_id="message_update",
            source_ref=update_ref,
            text_value=f"Update {created.commitment_id}: I will bring the keys Saturday.",
        )
        update = make_candidate(
            operation=CommitmentOperation.UPDATE,
            source_ref=update_ref,
            quote="I will bring the keys Saturday",
            target_id=created.commitment_id,
        ).model_copy(update={"summary": "Bring the keys Saturday"})

        result = self.ledger.apply(update_event, ExtractionEnvelope(commitments=[update])).upserted[
            0
        ]

        self.assertEqual(result.summary, "Bring the keys Saturday")
        self.assertEqual(len(result.source_message_refs), 2)
        self.assertEqual(
            [item["event_type"] for item in self.ledger.audit_events(event.organization_id)],
            ["created", "updated"],
        )
        with self.engine.connect() as connection:
            version = connection.execute(
                text(
                    "SELECT version FROM commitments "
                    "WHERE organization_id=:org AND commitment_id=:commitment"
                ),
                {"org": event.organization_id, "commitment": created.commitment_id},
            ).scalar_one()
        self.assertEqual(version, 2)

    def test_audit_table_rejects_update_and_delete(self) -> None:
        event = make_event()
        self.ledger.apply(event, ExtractionEnvelope(commitments=[make_candidate()]))

        for statement in (
            "UPDATE commitment_events SET event_type='updated'",
            "DELETE FROM commitment_events",
        ):
            with self.assertRaises(DatabaseError), self.engine.begin() as connection:
                connection.execute(text(statement))


if __name__ == "__main__":
    unittest.main()
