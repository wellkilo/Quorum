from __future__ import annotations

import unittest
from datetime import datetime

from quorum.executor_tools import build_executor_tools
from quorum.models import (
    CalendarActionInput,
    EmailDraftActionInput,
    ExecutionProvider,
    ExecutionReceipt,
    ExecutionStatus,
    FormActionInput,
)


class RecordingExecutionService:
    def __init__(self) -> None:
        self.calendar: CalendarActionInput | None = None
        self.email: EmailDraftActionInput | None = None
        self.form: FormActionInput | None = None

    def create_tentative_event(self, action: CalendarActionInput) -> ExecutionReceipt:
        self.calendar = action
        return self._receipt(
            action.organization_id, action.action_id, "calendar_create_tentative_event"
        )

    def create_email_draft(self, action: EmailDraftActionInput) -> ExecutionReceipt:
        self.email = action
        return self._receipt(action.organization_id, action.action_id, "gmail_create_draft")

    def create_response_request(self, action: FormActionInput) -> ExecutionReceipt:
        self.form = action
        return self._receipt(
            action.organization_id,
            action.action_id,
            "forms_create_response_request",
        )

    @staticmethod
    def _receipt(organization_id: str, action_id: str, tool_name: str) -> ExecutionReceipt:
        executed_at = datetime.fromisoformat("2026-08-27T09:00:00+00:00")
        return ExecutionReceipt(
            organization_id=organization_id,
            action_id=action_id,
            tool_name=tool_name,
            provider=ExecutionProvider.GOOGLE_CALENDAR,
            external_resource_id="resource_123",
            status=ExecutionStatus.EXECUTED,
            reversible=True,
            executed_at=executed_at,
            undo_expires_at=executed_at,
            undo_url="https://demo.example/actions/undo?token=opaque",
        )


class ExecutorToolTest(unittest.TestCase):
    def test_real_tools_have_stable_names_and_strict_input_schemas(self) -> None:
        service = RecordingExecutionService()
        tools = build_executor_tools(service)

        self.assertEqual(
            [item.tool_name for item in tools],
            [
                "calendar_create_tentative_event",
                "gmail_create_draft",
                "forms_create_response_request",
            ],
        )
        for item in tools:
            properties = item.tool_spec["inputSchema"]["json"]["properties"]
            self.assertIn("organization_id", properties)
            self.assertIn("action_id", properties)

    def test_calendar_tool_parses_iso_timestamps_into_typed_action(self) -> None:
        service = RecordingExecutionService()
        calendar_tool = build_executor_tools(service)[0]

        output = calendar_tool(
            organization_id="org_test",
            action_id="action_calendar",
            title="Tentative planning",
            starts_at="2026-08-28T09:00:00+08:00",
            ends_at="2026-08-28T10:00:00+08:00",
            time_zone="Asia/Shanghai",
        )

        self.assertEqual(output["status"], "executed")
        self.assertIsNotNone(service.calendar)
        assert service.calendar is not None
        self.assertIsInstance(service.calendar.starts_at, datetime)
        self.assertEqual(service.calendar.starts_at.utcoffset().total_seconds(), 8 * 60 * 60)

    def test_form_tool_validates_questions_before_execution(self) -> None:
        service = RecordingExecutionService()
        form_tool = build_executor_tools(service)[2]

        form_tool(
            organization_id="org_test",
            action_id="action_form",
            title="Availability",
            questions=[{"title": "Can you attend?", "required": True}],
        )

        self.assertIsNotNone(service.form)
        assert service.form is not None
        self.assertEqual(service.form.questions[0].title, "Can you attend?")
        self.assertTrue(service.form.questions[0].required)


if __name__ == "__main__":
    unittest.main()
