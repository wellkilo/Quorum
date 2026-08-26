from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quorum.database import DatabaseSettings, create_database_engine
from quorum.decision_store import DecisionPolicyStore
from quorum.execution import (
    ActionExecutionService,
    ExecutionConfigurationError,
    ExecutionConflictError,
    ExecutionNotAuthorizedError,
    ExecutionStore,
    UndoTokenError,
    UndoTokenSigner,
    build_action_execution_service,
)
from quorum.models import (
    ActionRequest,
    AutonomyLevel,
    CalendarActionInput,
    ExecutionReceipt,
    ImpactRadius,
    InterruptResolution,
    MoneyImpact,
    ParticipantResponse,
    Reversibility,
    TaskClass,
)
from quorum.providers import ProviderOperationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 27, 9, tzinfo=UTC)


class FakeCalendarProvider:
    def __init__(self, failure: ProviderOperationError | None = None) -> None:
        self.failure = failure
        self.created: list[CalendarActionInput] = []
        self.deleted: list[str] = []

    def create_tentative_event(self, action: CalendarActionInput) -> tuple[str, str | None]:
        self.created.append(action)
        if self.failure is not None:
            raise self.failure
        return "event_123", "https://calendar.google.com/event?eid=opaque"

    def delete_event(self, external_resource_id: str) -> None:
        self.deleted.append(external_resource_id)


class UnusedGmailProvider:
    def create_draft(self, _action: object) -> tuple[str, str | None]:
        raise AssertionError("Gmail must not be called")

    def delete_draft(self, _external_resource_id: str) -> None:
        raise AssertionError("Gmail must not be called")


class UnusedFormProvider:
    def create_response_request(self, _action: object) -> tuple[str, str | None]:
        raise AssertionError("Forms must not be called")

    def delete_form(self, _external_resource_id: str) -> None:
        raise AssertionError("Forms must not be called")


class FakeSlackNotifier:
    def __init__(self) -> None:
        self.receipts: list[tuple[str, str]] = []

    def send_group_receipt(self, channel_id: str, receipt: ExecutionReceipt) -> str:
        self.receipts.append((channel_id, receipt.action_id))
        return "1780000000.000100"


class ExecutionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "execution.sqlite3"
        self.engine = create_database_engine(
            DatabaseSettings(url=f"sqlite+pysqlite:///{self.path}")
        )
        config = Config(PROJECT_ROOT / "alembic.ini")
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        self.policy = DecisionPolicyStore(self.engine)
        self.execution_store = ExecutionStore(self.engine)
        self.signer = UndoTokenSigner(b"stage-three-test-secret-is-at-least-32-bytes")
        self.calendar = FakeCalendarProvider()
        self.slack = FakeSlackNotifier()
        self.service = ActionExecutionService(
            self.execution_store,
            self.signer,
            public_base_url="https://demo.example",
            calendar=self.calendar,
            gmail=UnusedGmailProvider(),
            forms=UnusedFormProvider(),
            slack=self.slack,
            clock=lambda: NOW,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def make_action(
        self,
        *,
        action_id: str = "action_calendar",
        title: str = "Tentative neighborhood planning",
        receipt_channel_id: str | None = "C_GROUP",
    ) -> CalendarActionInput:
        return CalendarActionInput(
            organization_id="org_test",
            action_id=action_id,
            title=title,
            starts_at=datetime(2026, 8, 28, 9, tzinfo=UTC),
            ends_at=datetime(2026, 8, 28, 10, tzinfo=UTC),
            time_zone="Asia/Shanghai",
            receipt_channel_id=receipt_channel_id,
        )

    def authorize(
        self,
        action: CalendarActionInput,
        *,
        reversibility: Reversibility = Reversibility.REVERSIBLE,
    ) -> None:
        arguments = action.model_dump(
            mode="json",
            exclude={"organization_id", "action_id"},
            exclude_none=True,
        )
        deciders = ["person_a", "person_b"]
        decision = self.policy.decide(
            ActionRequest(
                action_id=action.action_id,
                organization_id=action.organization_id,
                requested_by_id="person_requester",
                action_class=TaskClass.EVENT_DECISION,
                tool_name="calendar_create_tentative_event",
                summary="Create a tentative planning event",
                reversibility=reversibility,
                impact_radius=ImpactRadius.INDIVIDUAL,
                money_impact=MoneyImpact.NONE,
                candidate_decider_ids=deciders,
                action_arguments=arguments,
                requested_at=NOW,
            ),
            now=NOW,
        )
        if decision.required_quorum:
            self.policy.resolve(
                action.organization_id,
                InterruptResolution(
                    action_id=action.action_id,
                    responses=[
                        ParticipantResponse(participant_id=person_id, decision="approve")
                        for person_id in decision.selected_decider_ids
                    ],
                ),
                now=NOW,
            )

    @staticmethod
    def token_from_url(undo_url: str | None) -> str:
        if undo_url is None:
            raise AssertionError("undo URL was not created")
        return parse_qs(urlparse(undo_url).query)["token"][0]

    def test_successful_execution_is_idempotent_and_persists_safe_receipt(self) -> None:
        action = self.make_action()
        self.authorize(action)

        first = self.service.create_tentative_event(action)
        second = self.service.create_tentative_event(action)

        self.assertEqual(first, second)
        self.assertEqual(len(self.calendar.created), 1)
        self.assertEqual(self.slack.receipts, [("C_GROUP", "action_calendar")])
        row = self.execution_store.get("org_test", "action_calendar")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.receipt_message_ts, "1780000000.000100")
        database_bytes = self.path.read_bytes()
        self.assertNotIn(b"Tentative neighborhood planning", database_bytes)
        self.assertNotIn(b"Asia/Shanghai", database_bytes)

    def test_changed_arguments_are_rejected_before_provider_call(self) -> None:
        approved = self.make_action()
        self.authorize(approved)
        changed = self.make_action(title="Changed after approval")

        with self.assertRaises(ExecutionNotAuthorizedError):
            self.service.create_tentative_event(changed)

        self.assertEqual(self.calendar.created, [])

    def test_undo_calls_provider_once_consumes_token_and_downgrades_autonomy(self) -> None:
        action = self.make_action()
        self.authorize(action)
        receipt = self.service.create_tentative_event(action)
        token = self.token_from_url(receipt.undo_url)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE autonomy_profiles SET level=2, consecutive_approvals=2 "
                    "WHERE organization_id='org_test' AND action_class='event_decision'"
                )
            )

        result = self.service.undo(token)

        self.assertEqual(result.status.value, "undone")
        self.assertEqual(self.calendar.deleted, ["event_123"])
        autonomy = self.policy.autonomy_for("org_test", TaskClass.EVENT_DECISION)
        self.assertIs(autonomy.level, AutonomyLevel.SUGGEST)
        self.assertEqual(autonomy.consecutive_approvals, 0)
        self.assertEqual(autonomy.undo_count, 1)
        with self.assertRaisesRegex(UndoTokenError, "already used"):
            self.service.undo(token)
        self.assertEqual(self.calendar.deleted, ["event_123"])

    def test_tampered_and_expired_tokens_fail_closed(self) -> None:
        valid = self.signer.issue("org_test", "action_test", NOW + timedelta(hours=1))
        expired = self.signer.issue("org_test", "action_test", NOW - timedelta(seconds=1))

        with self.assertRaisesRegex(UndoTokenError, "invalid"):
            self.signer.verify(valid[:-1] + ("0" if valid[-1] != "0" else "1"), now=NOW)
        with self.assertRaisesRegex(UndoTokenError, "expired"):
            self.signer.verify(expired, now=NOW)

    def test_irreversible_policy_cannot_enter_phase_three_execution(self) -> None:
        action = self.make_action(action_id="action_irreversible")
        self.authorize(action, reversibility=Reversibility.IRREVERSIBLE)

        with self.assertRaisesRegex(ExecutionNotAuthorizedError, "reversible"):
            self.service.create_tentative_event(action)

        self.assertEqual(self.calendar.created, [])

    def test_uncertain_provider_outcome_is_not_retried_automatically(self) -> None:
        action = self.make_action(action_id="action_uncertain")
        self.authorize(action)
        failure = ProviderOperationError("calendar_create_transport_error", outcome_uncertain=True)
        self.calendar.failure = failure

        with self.assertRaises(ProviderOperationError):
            self.service.create_tentative_event(action)
        with self.assertRaisesRegex(ExecutionConflictError, "uncertain"):
            self.service.create_tentative_event(action)

        self.assertEqual(len(self.calendar.created), 1)
        row = self.execution_store.get("org_test", "action_uncertain")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.status, "uncertain")
        self.assertEqual(row.error_code, "calendar_create_transport_error")

    def test_execution_audit_events_are_database_append_only(self) -> None:
        action = self.make_action(action_id="action_audit")
        self.authorize(action)
        self.service.create_tentative_event(action)

        with self.assertRaises(DatabaseError), self.engine.begin() as connection:
            connection.execute(text("UPDATE execution_events SET event_type='failed'"))
        with self.assertRaises(DatabaseError), self.engine.begin() as connection:
            connection.execute(text("DELETE FROM execution_events"))

    def test_production_factory_requires_an_https_public_origin(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "QUORUM_PUBLIC_BASE_URL": "http://demo.example",
                    "QUORUM_UNDO_SIGNING_SECRET": "stage-three-test-secret-is-at-least-32-bytes",
                    "QUORUM_SLACK_BOT_TOKEN": "synthetic-placeholder-token",
                },
                clear=True,
            ),
            self.assertRaisesRegex(ExecutionConfigurationError, "HTTPS origin"),
        ):
            build_action_execution_service(self.engine)


if __name__ == "__main__":
    unittest.main()
