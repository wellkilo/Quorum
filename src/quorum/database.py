"""Transactional SQLAlchemy ledger for SQLite development and PostgreSQL production."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, select
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from quorum.db_models import (
    CommitmentEventRow,
    CommitmentRow,
    OrganizationRow,
    ProcessedMessageRow,
)
from quorum.ledger import apply_candidate, evidence_rejection_code
from quorum.models import (
    CanonicalMessageEvent,
    CommitmentCandidate,
    CommitmentOperation,
    CommitmentStatus,
    ExtractionEnvelope,
    LedgerChangeSet,
    LedgerItem,
    RejectedCandidate,
    TaskClass,
)

SUPPORTED_DRIVERS = {"postgresql+psycopg", "sqlite", "sqlite+pysqlite"}


class DatabaseConfigurationError(ValueError):
    """Raised when the configured database is unsafe or unsupported."""


class IdempotencyConflictError(RuntimeError):
    """Raised when one message identity is reused for different canonical content."""


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    url: str

    @classmethod
    def from_environment(cls) -> DatabaseSettings:
        raw_url = os.environ.get("QUORUM_DATABASE_URL", "").strip()
        if not raw_url:
            return cls(url="sqlite+pysqlite:///./var/quorum.sqlite3")
        if raw_url.startswith("postgres://"):
            raw_url = "postgresql+psycopg://" + raw_url.removeprefix("postgres://")
        elif raw_url.startswith("postgresql://"):
            raw_url = "postgresql+psycopg://" + raw_url.removeprefix("postgresql://")
        driver = make_url(raw_url).drivername
        if driver not in SUPPORTED_DRIVERS:
            raise DatabaseConfigurationError(
                "QUORUM_DATABASE_URL must use PostgreSQL with psycopg 3 or SQLite"
            )
        return cls(url=raw_url)


def create_database_engine(settings: DatabaseSettings) -> Engine:
    url = make_url(settings.url)
    connect_args: dict[str, Any] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        database_path = url.database
        if database_path and database_path != ":memory:":
            Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    else:
        connect_args.update(
            {
                "application_name": "quorum",
                "options": "-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=5000",
            }
        )
    engine = create_engine(
        settings.url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
        hide_parameters=True,
    )
    if engine.dialect.name == "sqlite":
        sqlalchemy_event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _event_id(
    event: CanonicalMessageEvent, candidate: CommitmentCandidate, candidate_index: int
) -> str:
    material = "|".join(
        (
            event.organization_id,
            event.source.source_message_ref,
            str(candidate_index),
            candidate.operation.value,
        )
    )
    return f"evt_{sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _event_fingerprint(event: CanonicalMessageEvent) -> str:
    canonical = event.model_dump_json(exclude_none=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _to_item(row: CommitmentRow) -> LedgerItem:
    created_at = _normalize_datetime(row.created_at)
    updated_at = _normalize_datetime(row.updated_at)
    if created_at is None or updated_at is None:
        raise ValueError("persisted commitment timestamps cannot be null")
    return LedgerItem(
        commitment_id=row.commitment_id,
        organization_id=row.organization_id,
        task_class=TaskClass(row.task_class),
        summary=row.summary,
        owner_id=row.owner_id,
        due_at=_normalize_datetime(row.due_at),
        status=CommitmentStatus(row.status),
        source_message_refs=row.source_message_refs,
        created_at=created_at,
        updated_at=updated_at,
        confidence=row.confidence,
    )


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _copy_to_row(row: CommitmentRow, item: LedgerItem, *, version: int) -> None:
    row.task_class = item.task_class.value
    row.summary = item.summary
    row.owner_id = item.owner_id
    row.due_at = item.due_at
    row.status = item.status.value
    row.source_message_refs = item.source_message_refs
    row.created_at = item.created_at
    row.updated_at = item.updated_at
    row.confidence = item.confidence
    row.version = version


class DatabaseLedger:
    """Tenant-isolated ledger with message idempotency and append-only audit events."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name not in {"postgresql", "sqlite"}:
            raise DatabaseConfigurationError("DatabaseLedger supports PostgreSQL and SQLite only")
        self._engine = engine
        self._session_factory = sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_environment(cls) -> DatabaseLedger:
        return cls(create_database_engine(DatabaseSettings.from_environment()))

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> DatabaseLedger:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get(self, organization_id: str, commitment_id: str) -> LedgerItem | None:
        with self._session_factory() as session:
            row = session.get(CommitmentRow, (organization_id, commitment_id))
            return _to_item(row) if row is not None else None

    def values(self, organization_id: str) -> tuple[LedgerItem, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(CommitmentRow)
                .where(CommitmentRow.organization_id == organization_id)
                .order_by(CommitmentRow.commitment_id)
            ).all()
            return tuple(_to_item(row) for row in rows)

    def audit_events(self, organization_id: str) -> tuple[dict[str, Any], ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(CommitmentEventRow)
                .where(CommitmentEventRow.organization_id == organization_id)
                .order_by(CommitmentEventRow.created_at, CommitmentEventRow.event_id)
            ).all()
            return tuple(
                {
                    "event_id": row.event_id,
                    "commitment_id": row.commitment_id,
                    "event_type": row.event_type,
                    "source_message_ref": row.source_message_ref,
                    "snapshot": row.snapshot,
                }
                for row in rows
            )

    def apply(
        self,
        event: CanonicalMessageEvent,
        extraction: ExtractionEnvelope,
        *,
        now: datetime | None = None,
    ) -> LedgerChangeSet:
        applied_at = now or datetime.now(UTC)
        with self._session_factory.begin() as session:
            self._ensure_organization(session, event.organization_id, applied_at)
            if not self._claim_message(session, event, applied_at):
                return LedgerChangeSet(duplicate_event=True)

            changes = LedgerChangeSet()
            for index, candidate in enumerate(extraction.commitments):
                rejection = evidence_rejection_code(event, candidate)
                if rejection is not None:
                    changes.rejected.append(
                        RejectedCandidate(candidate_index=index, code=rejection)
                    )
                    continue

                target_row = self._load_target(session, event, candidate)
                if candidate.operation is not CommitmentOperation.CREATE and target_row is None:
                    changes.rejected.append(
                        RejectedCandidate(candidate_index=index, code="TARGET_NOT_FOUND")
                    )
                    continue
                target = _to_item(target_row) if target_row is not None else None
                item = apply_candidate(event, candidate, target=target, applied_at=applied_at)
                row = target_row or CommitmentRow(
                    organization_id=item.organization_id,
                    commitment_id=item.commitment_id,
                )
                next_version = row.version + 1 if target_row is not None else 1
                _copy_to_row(row, item, version=next_version)
                if target_row is None:
                    session.add(row)
                session.flush()
                session.add(
                    CommitmentEventRow(
                        event_id=_event_id(event, candidate, index),
                        organization_id=event.organization_id,
                        commitment_id=item.commitment_id,
                        source_message_ref=event.source.source_message_ref,
                        actor_id=event.actor_id,
                        event_type={
                            CommitmentOperation.CREATE: "created",
                            CommitmentOperation.UPDATE: "updated",
                            CommitmentOperation.CANCEL: "cancelled",
                        }[candidate.operation],
                        snapshot=item.model_dump(mode="json"),
                        created_at=applied_at,
                    )
                )
                changes.upserted.append(item)
            return changes

    def _ensure_organization(
        self, session: Session, organization_id: str, created_at: datetime
    ) -> None:
        values = {"organization_id": organization_id, "created_at": created_at}
        if self._engine.dialect.name == "postgresql":
            session.execute(
                postgresql_insert(OrganizationRow).values(**values).on_conflict_do_nothing()
            )
        else:
            session.execute(
                sqlite_insert(OrganizationRow).values(**values).on_conflict_do_nothing()
            )

    def _claim_message(
        self, session: Session, event: CanonicalMessageEvent, processed_at: datetime
    ) -> bool:
        values = {
            "organization_id": event.organization_id,
            "source_message_ref": event.source.source_message_ref,
            "message_id": event.message_id,
            "event_fingerprint": _event_fingerprint(event),
            "actor_id": event.actor_id,
            "occurred_at": event.occurred_at,
            "processed_at": processed_at,
            "data_classification": event.data_classification.value,
        }
        if self._engine.dialect.name == "postgresql":
            result = session.execute(
                postgresql_insert(ProcessedMessageRow)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(ProcessedMessageRow.source_message_ref)
            )
        else:
            result = session.execute(
                sqlite_insert(ProcessedMessageRow)
                .values(**values)
                .on_conflict_do_nothing()
                .returning(ProcessedMessageRow.source_message_ref)
            )
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return True
        existing = session.get(
            ProcessedMessageRow,
            (event.organization_id, event.source.source_message_ref),
        )
        if existing is None or existing.event_fingerprint != values["event_fingerprint"]:
            raise IdempotencyConflictError(
                "message identity was reused with different canonical content"
            )
        return False

    @staticmethod
    def _load_target(
        session: Session, event: CanonicalMessageEvent, candidate: CommitmentCandidate
    ) -> CommitmentRow | None:
        if candidate.operation is CommitmentOperation.CREATE:
            return None
        return session.scalar(
            select(CommitmentRow)
            .where(
                CommitmentRow.organization_id == event.organization_id,
                CommitmentRow.commitment_id == candidate.target_commitment_id,
            )
            .with_for_update()
        )
