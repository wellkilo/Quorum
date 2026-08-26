from __future__ import annotations

import base64
import unittest
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from unittest.mock import MagicMock

from quorum.models import (
    CalendarActionInput,
    EmailDraftActionInput,
    FormActionInput,
    FormQuestion,
)
from quorum.providers import (
    GoogleCalendarProvider,
    GoogleFormProvider,
    GoogleGmailDraftProvider,
    ProviderOperationError,
)


class GoogleProviderTest(unittest.TestCase):
    def test_calendar_uses_tentative_insert_and_reversible_delete_contract(self) -> None:
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {
            "id": "event_123",
            "htmlLink": "https://calendar.google.com/event?eid=opaque",
        }
        service.events.return_value.delete.return_value.execute.return_value = None
        provider = GoogleCalendarProvider(service)
        action = CalendarActionInput(
            organization_id="org_test",
            action_id="action_calendar",
            title="Tentative neighborhood planning",
            starts_at=datetime(2026, 8, 28, 9, tzinfo=UTC),
            ends_at=datetime(2026, 8, 28, 10, tzinfo=UTC),
            time_zone="Asia/Shanghai",
        )

        resource_id, external_url = provider.create_tentative_event(action)
        provider.delete_event(resource_id)

        self.assertEqual(resource_id, "event_123")
        self.assertEqual(external_url, "https://calendar.google.com/event?eid=opaque")
        service.events.return_value.insert.assert_called_once_with(
            calendarId="primary",
            sendUpdates="none",
            body={
                "summary": "Tentative neighborhood planning",
                "status": "tentative",
                "start": {
                    "dateTime": "2026-08-28T09:00:00+00:00",
                    "timeZone": "Asia/Shanghai",
                },
                "end": {
                    "dateTime": "2026-08-28T10:00:00+00:00",
                    "timeZone": "Asia/Shanghai",
                },
            },
        )
        service.events.return_value.delete.assert_called_once_with(
            calendarId="primary",
            eventId="event_123",
            sendUpdates="none",
        )

    def test_gmail_creates_a_draft_without_calling_send(self) -> None:
        service = MagicMock()
        drafts = service.users.return_value.drafts.return_value
        drafts.create.return_value.execute.return_value = {"id": "draft_123"}
        drafts.delete.return_value.execute.return_value = None
        provider = GoogleGmailDraftProvider(service)
        action = EmailDraftActionInput(
            organization_id="org_test",
            action_id="action_email",
            recipient="volunteer@example.org",
            subject="Tentative supply handoff",
            body_text="Please review this draft before anyone sends it.",
        )

        resource_id, external_url = provider.create_draft(action)
        provider.delete_draft(resource_id)

        self.assertEqual(resource_id, "draft_123")
        self.assertIsNone(external_url)
        create_body = drafts.create.call_args.kwargs["body"]
        encoded = create_body["message"]["raw"]
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(encoded))
        self.assertEqual(message["To"], "volunteer@example.org")
        self.assertEqual(message["Subject"], "Tentative supply handoff")
        self.assertEqual(
            message.get_content().strip(), "Please review this draft before anyone sends it."
        )
        drafts.create.assert_called_once()
        drafts.delete.assert_called_once_with(userId="me", id="draft_123")
        drafts.send.assert_not_called()

    def test_forms_uses_create_batch_update_and_drive_delete(self) -> None:
        forms_service = MagicMock()
        drive_service = MagicMock()
        forms = forms_service.forms.return_value
        forms.create.return_value.execute.return_value = {"formId": "form_123"}
        forms.batchUpdate.return_value.execute.return_value = {
            "form": {"responderUri": "https://docs.google.com/forms/d/e/opaque/viewform"}
        }
        drive_service.files.return_value.delete.return_value.execute.return_value = None
        provider = GoogleFormProvider(forms_service, drive_service)
        action = FormActionInput(
            organization_id="org_test",
            action_id="action_form",
            title="Neighborhood availability",
            questions=[
                FormQuestion(title="Can you attend?", required=True),
                FormQuestion(title="Any constraints?"),
            ],
        )

        resource_id, external_url = provider.create_response_request(action)
        provider.delete_form(resource_id)

        self.assertEqual(resource_id, "form_123")
        self.assertEqual(external_url, "https://docs.google.com/forms/d/e/opaque/viewform")
        forms.create.assert_called_once_with(
            body={
                "info": {
                    "title": "Neighborhood availability",
                    "documentTitle": "Neighborhood availability",
                }
            }
        )
        update_body = forms.batchUpdate.call_args.kwargs["body"]
        self.assertTrue(update_body["includeFormInResponse"])
        self.assertEqual(len(update_body["requests"]), 2)
        self.assertTrue(
            update_body["requests"][0]["createItem"]["item"]["questionItem"]["question"]["required"]
        )
        drive_service.files.return_value.delete.assert_called_once_with(fileId="form_123")

    def test_forms_configuration_failure_deletes_the_partially_created_form(self) -> None:
        forms_service = MagicMock()
        drive_service = MagicMock()
        forms = forms_service.forms.return_value
        forms.create.return_value.execute.return_value = {"formId": "form_partial"}
        forms.batchUpdate.return_value.execute.side_effect = RuntimeError("private provider detail")
        drive_service.files.return_value.delete.return_value.execute.return_value = None
        provider = GoogleFormProvider(forms_service, drive_service)
        action = FormActionInput(
            organization_id="org_test",
            action_id="action_form_failed",
            title="Availability",
            questions=[FormQuestion(title="Can you attend?")],
        )

        with self.assertRaises(ProviderOperationError) as raised:
            provider.create_response_request(action)

        self.assertEqual(raised.exception.code, "forms_batch_update_transport_error")
        self.assertTrue(raised.exception.outcome_uncertain)
        self.assertNotIn("private provider detail", str(raised.exception))
        drive_service.files.return_value.delete.assert_called_once_with(fileId="form_partial")

    def test_missing_resource_id_and_transport_failure_are_outcome_uncertain(self) -> None:
        missing_id_service = MagicMock()
        missing_id_service.events.return_value.insert.return_value.execute.return_value = {}
        transport_service = MagicMock()
        create_request = (
            transport_service.users.return_value.drafts.return_value.create.return_value.execute
        )
        create_request.side_effect = RuntimeError("credential detail must not escape")
        calendar = GoogleCalendarProvider(missing_id_service)
        gmail = GoogleGmailDraftProvider(transport_service)
        calendar_action = CalendarActionInput(
            organization_id="org_test",
            action_id="action_missing",
            title="Tentative event",
            starts_at=datetime(2026, 8, 28, 9, tzinfo=UTC),
            ends_at=datetime(2026, 8, 28, 10, tzinfo=UTC),
        )
        email_action = EmailDraftActionInput(
            organization_id="org_test",
            action_id="action_transport",
            recipient="volunteer@example.org",
            subject="Draft",
            body_text="Review this.",
        )

        with self.assertRaises(ProviderOperationError) as missing:
            calendar.create_tentative_event(calendar_action)
        with self.assertRaises(ProviderOperationError) as transport:
            gmail.create_draft(email_action)

        self.assertEqual(missing.exception.code, "calendar_create_missing_id")
        self.assertTrue(missing.exception.outcome_uncertain)
        self.assertEqual(transport.exception.code, "gmail_draft_create_transport_error")
        self.assertTrue(transport.exception.outcome_uncertain)
        self.assertNotIn("credential detail", str(transport.exception))


if __name__ == "__main__":
    unittest.main()
