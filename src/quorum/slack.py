"""Slack delivery for Quorum's three allowed interaction surfaces."""

from __future__ import annotations

import os
import re
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from quorum.models import DataClassification, ExecutionReceipt, PolicyDecision, WeeklySummary

_SAFE_ERROR = re.compile(r"^[a-z0-9_]{1,100}$")


class SlackDeliveryError(RuntimeError):
    """Sanitized Slack delivery error that never includes message content or tokens."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SlackNotifier:
    def __init__(self, client: Any) -> None:
        self._client = client

    def send_group_receipt(
        self,
        channel_id: str,
        receipt: ExecutionReceipt,
        *,
        data_classification: DataClassification | None = None,
    ) -> str:
        provider_label = receipt.provider.value.replace("_", " ")
        prefix = _classification_prefix(data_classification)
        text = f"{prefix}Quorum completed one reversible {provider_label} action."
        open_label = "Open demo" if data_classification is DataClassification.SYNTHETIC else "Open"
        undo_label = (
            "Undo preview" if data_classification is DataClassification.SYNTHETIC else "Undo"
        )
        elements: list[dict[str, Any]] = []
        if receipt.external_url is not None:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": open_label},
                    "action_id": "quorum_open_action",
                    "url": receipt.external_url,
                }
            )
        if receipt.undo_url is not None:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": undo_label},
                    "action_id": "quorum_undo_action",
                    "url": receipt.undo_url,
                    "style": "danger",
                }
            )
        blocks: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}}
        ]
        if elements:
            blocks.append({"type": "actions", "elements": elements})
        response = self._call("chat_postMessage", channel=channel_id, text=text, blocks=blocks)
        timestamp = response.get("ts")
        if not isinstance(timestamp, str) or not timestamp:
            raise SlackDeliveryError("missing_message_timestamp")
        return timestamp

    def send_private_question(
        self,
        participant_id: str,
        decision: PolicyDecision,
        *,
        data_classification: DataClassification | None = None,
    ) -> str:
        opened = self._call("conversations_open", users=participant_id)
        channel = opened.get("channel")
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        if not isinstance(channel_id, str) or not channel_id:
            raise SlackDeliveryError("missing_direct_message_channel")
        timeout = decision.timeout_at.isoformat() if decision.timeout_at is not None else "none"
        text = _classification_prefix(data_classification) + (
            f"Quorum needs your decision for action `{decision.action_id}`. "
            f"Risk: {decision.risk.tier.value}. Reply approve or reject by {timeout}; "
            f"default: {decision.timeout_default.value}."
        )
        response = self._call("chat_postMessage", channel=channel_id, text=text)
        timestamp = response.get("ts")
        if not isinstance(timestamp, str) or not timestamp:
            raise SlackDeliveryError("missing_message_timestamp")
        return timestamp

    def send_weekly_summary(self, channel_id: str, summary: WeeklySummary) -> str:
        """Send the single compact weekly summary allowed by the interaction contract."""

        provenance = (
            "Synthetic demonstration data; not a measured real-world outcome."
            if summary.data_classification is DataClassification.SYNTHETIC
            else "Redacted organization metrics."
        )
        text = (
            f"{_classification_prefix(summary.data_classification)}Quorum weekly summary for "
            f"{summary.week_ending.isoformat()}: {summary.closed_decisions} decisions closed, "
            f"{summary.interruption_count} interruptions."
        )
        fields = [
            {"type": "mrkdwn", "text": f"*Closed decisions*\n{summary.closed_decisions}"},
            {
                "type": "mrkdwn",
                "text": (
                    f"*Decision latency P50*\n{_format_hours(summary.decision_latency_p50_hours)}"
                ),
            },
            {
                "type": "mrkdwn",
                "text": f"*Total interruptions*\n{summary.interruption_count}",
            },
            {
                "type": "mrkdwn",
                "text": f"*People interrupted*\n{summary.people_interrupted}",
            },
            {
                "type": "mrkdwn",
                "text": (
                    "*Max per person*\n"
                    f"{summary.max_interruptions_per_person}/"
                    f"{summary.interrupt_budget_limit_per_person}"
                ),
            },
            {
                "type": "mrkdwn",
                "text": f"*Undo rate*\n{summary.undo_rate:.1%}",
            },
        ]
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Quorum week ending {summary.week_ending.isoformat()}",
                },
            },
            {"type": "section", "fields": fields},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Interrupt Budget: no more than "
                            f"{summary.interrupt_budget_limit_per_person} decision requests per "
                            f"person in a rolling 7-day window. {provenance}"
                        ),
                    }
                ],
            },
        ]
        response = self._call("chat_postMessage", channel=channel_id, text=text, blocks=blocks)
        timestamp = response.get("ts")
        if not isinstance(timestamp, str) or not timestamp:
            raise SlackDeliveryError("missing_message_timestamp")
        return timestamp

    def _call(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = getattr(self._client, method_name)(**kwargs)
        except SlackApiError as exc:
            raw_code = exc.response.get("error") if exc.response is not None else None
            code = (
                raw_code
                if isinstance(raw_code, str) and _SAFE_ERROR.fullmatch(raw_code)
                else "api_error"
            )
            raise SlackDeliveryError(code) from exc
        except Exception as exc:
            raise SlackDeliveryError("transport_error") from exc
        data = response.data if hasattr(response, "data") else response
        if not isinstance(data, dict) or not data.get("ok", False):
            raise SlackDeliveryError("invalid_response")
        return data


def build_slack_notifier() -> SlackNotifier:
    token = os.environ.get("QUORUM_SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("QUORUM_SLACK_BOT_TOKEN is required")
    return SlackNotifier(WebClient(token=token))


def _classification_prefix(value: DataClassification | None) -> str:
    return "Synthetic demo — " if value is DataClassification.SYNTHETIC else ""


def _format_hours(value: float) -> str:
    return f"{value:g}h"
