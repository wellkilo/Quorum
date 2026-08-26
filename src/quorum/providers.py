"""Real Google Workspace provider adapters with narrow reversible operations."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Protocol

import google.auth
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from quorum.models import CalendarActionInput, EmailDraftActionInput, FormActionInput

GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/drive.file",
)


class ProviderOperationError(RuntimeError):
    """Sanitized provider failure with explicit retry certainty."""

    def __init__(self, code: str, *, outcome_uncertain: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.outcome_uncertain = outcome_uncertain


class CalendarProvider(Protocol):
    def create_tentative_event(self, action: CalendarActionInput) -> tuple[str, str | None]: ...

    def delete_event(self, external_resource_id: str) -> None: ...


class GmailDraftProvider(Protocol):
    def create_draft(self, action: EmailDraftActionInput) -> tuple[str, str | None]: ...

    def delete_draft(self, external_resource_id: str) -> None: ...


class FormProvider(Protocol):
    def create_response_request(self, action: FormActionInput) -> tuple[str, str | None]: ...

    def delete_form(self, external_resource_id: str) -> None: ...


def _execute(request: Any, operation: str) -> dict[str, Any]:
    try:
        response = request.execute()
    except HttpError as exc:
        status = int(getattr(exc.resp, "status", 0) or 0)
        raise ProviderOperationError(
            f"{operation}_http_{status or 'unknown'}",
            outcome_uncertain=status == 0 or status >= 500,
        ) from exc
    except Exception as exc:
        raise ProviderOperationError(
            f"{operation}_transport_error", outcome_uncertain=True
        ) from exc
    if response is None:
        return {}
    if not isinstance(response, dict):
        raise ProviderOperationError(f"{operation}_invalid_response", outcome_uncertain=True)
    return response


class GoogleCalendarProvider:
    """Create tentative events and reverse them with Calendar v3."""

    def __init__(self, service: Any, *, calendar_id: str = "primary") -> None:
        self._service = service
        self._calendar_id = calendar_id

    def create_tentative_event(self, action: CalendarActionInput) -> tuple[str, str | None]:
        response = _execute(
            self._service.events().insert(
                calendarId=self._calendar_id,
                sendUpdates="none",
                body={
                    "summary": action.title,
                    "status": "tentative",
                    "start": {
                        "dateTime": action.starts_at.isoformat(),
                        "timeZone": action.time_zone,
                    },
                    "end": {
                        "dateTime": action.ends_at.isoformat(),
                        "timeZone": action.time_zone,
                    },
                },
            ),
            "calendar_create",
        )
        resource_id = response.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            raise ProviderOperationError("calendar_create_missing_id", outcome_uncertain=True)
        external_url = response.get("htmlLink")
        return resource_id, external_url if isinstance(external_url, str) else None

    def delete_event(self, external_resource_id: str) -> None:
        _execute(
            self._service.events().delete(
                calendarId=self._calendar_id,
                eventId=external_resource_id,
                sendUpdates="none",
            ),
            "calendar_delete",
        )


class GoogleGmailDraftProvider:
    """Create Gmail drafts without sending mail, and delete those drafts on undo."""

    def __init__(self, service: Any, *, user_id: str = "me") -> None:
        self._service = service
        self._user_id = user_id

    def create_draft(self, action: EmailDraftActionInput) -> tuple[str, str | None]:
        message = EmailMessage()
        message["To"] = action.recipient
        message["Subject"] = action.subject
        message.set_content(action.body_text)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        response = _execute(
            self._service.users()
            .drafts()
            .create(userId=self._user_id, body={"message": {"raw": raw}}),
            "gmail_draft_create",
        )
        resource_id = response.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            raise ProviderOperationError("gmail_draft_create_missing_id", outcome_uncertain=True)
        return resource_id, None

    def delete_draft(self, external_resource_id: str) -> None:
        _execute(
            self._service.users().drafts().delete(userId=self._user_id, id=external_resource_id),
            "gmail_draft_delete",
        )


class GoogleFormProvider:
    """Create response-request forms and delete the created Drive file on undo."""

    def __init__(self, forms_service: Any, drive_service: Any) -> None:
        self._forms = forms_service
        self._drive = drive_service

    def create_response_request(self, action: FormActionInput) -> tuple[str, str | None]:
        created = _execute(
            self._forms.forms().create(
                body={"info": {"title": action.title, "documentTitle": action.title}}
            ),
            "forms_create",
        )
        form_id = created.get("formId")
        if not isinstance(form_id, str) or not form_id:
            raise ProviderOperationError("forms_create_missing_id", outcome_uncertain=True)
        requests = [
            {
                "createItem": {
                    "item": {
                        "title": question.title,
                        "questionItem": {
                            "question": {
                                "required": question.required,
                                "textQuestion": {"paragraph": False},
                            }
                        },
                    },
                    "location": {"index": index},
                }
            }
            for index, question in enumerate(action.questions)
        ]
        try:
            updated = _execute(
                self._forms.forms().batchUpdate(
                    formId=form_id,
                    body={"includeFormInResponse": True, "requests": requests},
                ),
                "forms_batch_update",
            )
        except ProviderOperationError as exc:
            try:
                self.delete_form(form_id)
            except ProviderOperationError as cleanup_exc:
                raise ProviderOperationError(
                    "forms_configuration_failed_cleanup_uncertain", outcome_uncertain=True
                ) from cleanup_exc
            raise exc
        form = updated.get("form")
        responder_uri = form.get("responderUri") if isinstance(form, dict) else None
        if not isinstance(responder_uri, str):
            responder_uri = created.get("responderUri")
        return form_id, responder_uri if isinstance(responder_uri, str) else None

    def delete_form(self, external_resource_id: str) -> None:
        _execute(
            self._drive.files().delete(fileId=external_resource_id),
            "forms_delete",
        )


def build_google_providers() -> tuple[
    GoogleCalendarProvider,
    GoogleGmailDraftProvider,
    GoogleFormProvider,
]:
    """Build providers from Application Default Credentials and least-scope clients."""

    credentials, _project = google.auth.default(scopes=list(GOOGLE_SCOPES))
    calendar = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    forms = build("forms", "v1", credentials=credentials, cache_discovery=False)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return (
        GoogleCalendarProvider(calendar),
        GoogleGmailDraftProvider(gmail),
        GoogleFormProvider(forms, drive),
    )
