"""Factory for real Strands tools backed by the action execution service."""

from __future__ import annotations

from typing import Protocol

from strands import tool
from strands.tools.decorator import DecoratedFunctionTool

from quorum.models import (
    CalendarActionInput,
    EmailDraftActionInput,
    ExecutionReceipt,
    FormActionInput,
    FormQuestion,
)


class ExecutorService(Protocol):
    def create_tentative_event(self, action: CalendarActionInput) -> ExecutionReceipt: ...

    def create_email_draft(self, action: EmailDraftActionInput) -> ExecutionReceipt: ...

    def create_response_request(self, action: FormActionInput) -> ExecutionReceipt: ...


def build_executor_tools(
    service: ExecutorService,
) -> list[DecoratedFunctionTool[..., dict[str, object]]]:
    @tool(name="calendar_create_tentative_event")
    def calendar_create_tentative_event(
        organization_id: str,
        action_id: str,
        title: str,
        starts_at: str,
        ends_at: str,
        time_zone: str = "UTC",
        receipt_channel_id: str | None = None,
    ) -> dict[str, object]:
        """Create a reversible tentative Google Calendar event.

        Args:
            organization_id: Opaque Quorum tenant identifier.
            action_id: The already authorized action identifier.
            title: Event title.
            starts_at: ISO 8601 start timestamp with timezone.
            ends_at: ISO 8601 end timestamp with timezone.
            time_zone: IANA timezone name.
            receipt_channel_id: Optional Slack channel for the one-line receipt.
        """

        receipt = service.create_tentative_event(
            CalendarActionInput.model_validate(
                {
                    "organization_id": organization_id,
                    "action_id": action_id,
                    "title": title,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "time_zone": time_zone,
                    "receipt_channel_id": receipt_channel_id,
                }
            )
        )
        return receipt.model_dump(mode="json")

    @tool(name="gmail_create_draft")
    def gmail_create_draft(
        organization_id: str,
        action_id: str,
        recipient: str,
        subject: str,
        body_text: str,
        receipt_channel_id: str | None = None,
    ) -> dict[str, object]:
        """Create a reversible Gmail draft without sending it.

        Args:
            organization_id: Opaque Quorum tenant identifier.
            action_id: The already authorized action identifier.
            recipient: Draft recipient email address.
            subject: Draft subject.
            body_text: Plain-text draft body.
            receipt_channel_id: Optional Slack channel for the one-line receipt.
        """

        receipt = service.create_email_draft(
            EmailDraftActionInput(
                organization_id=organization_id,
                action_id=action_id,
                recipient=recipient,
                subject=subject,
                body_text=body_text,
                receipt_channel_id=receipt_channel_id,
            )
        )
        return receipt.model_dump(mode="json")

    @tool(name="forms_create_response_request")
    def forms_create_response_request(
        organization_id: str,
        action_id: str,
        title: str,
        questions: list[dict[str, object]],
        receipt_channel_id: str | None = None,
    ) -> dict[str, object]:
        """Create a reversible Google Form response request.

        Args:
            organization_id: Opaque Quorum tenant identifier.
            action_id: The already authorized action identifier.
            title: Form title.
            questions: Text questions with title and required fields.
            receipt_channel_id: Optional Slack channel for the one-line receipt.
        """

        receipt = service.create_response_request(
            FormActionInput(
                organization_id=organization_id,
                action_id=action_id,
                title=title,
                questions=[FormQuestion.model_validate(question) for question in questions],
                receipt_channel_id=receipt_channel_id,
            )
        )
        return receipt.model_dump(mode="json")

    return [
        calendar_create_tentative_event,
        gmail_create_draft,
        forms_create_response_request,
    ]
