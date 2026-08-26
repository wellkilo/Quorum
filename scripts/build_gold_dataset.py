#!/usr/bin/env python3
"""Build the reviewable synthetic v1 commitment-extraction gold dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTPUT = Path("data/eval/commitment_gold_v1.jsonl")
BASE_TS = 1_780_000_000


def commitment(
    task_class: str,
    summary: str,
    quote: str,
    *,
    owner: str | None = None,
    due: str | None = None,
    operation: str = "create",
    target: str | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "task_class": task_class,
        "summary": summary,
        "owner_id": owner,
        "due_at": due,
        "target_commitment_id": target,
        "confidence": 1.0,
        "evidence_quote": quote,
    }


CASES: list[dict[str, Any]] = [
    {
        "text": "I will bring the storage keys by 2026-08-28 17:00 UTC.",
        "items": [
            commitment(
                "item_handoff",
                "Bring the storage keys",
                "I will bring the storage keys by 2026-08-28 17:00 UTC",
                owner="person_001",
                due="2026-08-28T17:00:00Z",
            )
        ],
        "tags": ["positive", "explicit-owner", "deadline"],
    },
    {
        "text": "I will drop the projector at the library desk by 2026-08-27 09:00 UTC.",
        "items": [
            commitment(
                "item_handoff",
                "Drop the projector at the library desk",
                "I will drop the projector at the library desk by 2026-08-27 09:00 UTC",
                owner="person_002",
                due="2026-08-27T09:00:00Z",
            )
        ],
        "tags": ["positive", "relative-time"],
    },
    {
        "text": "<@person_003> will pass the badge box to Omar by 2026-08-26 18:00 UTC.",
        "items": [
            commitment(
                "item_handoff",
                "Pass the badge box to Omar",
                "<@person_003> will pass the badge box to Omar by 2026-08-26 18:00 UTC",
                owner="person_003",
                due="2026-08-26T18:00:00Z",
            )
        ],
        "tags": ["positive", "delegated-owner"],
    },
    {
        "text": "I'll return the folding signs to room B12 after the event.",
        "items": [
            commitment(
                "item_handoff",
                "Return the folding signs to room B12",
                "I'll return the folding signs to room B12 after the event",
                owner="person_004",
            )
        ],
        "tags": ["positive", "no-deadline"],
    },
    {
        "text": "I have the donation receipts and will hand them to Jo at 12:00 UTC today.",
        "items": [
            commitment(
                "item_handoff",
                "Hand the donation receipts to Jo",
                "will hand them to Jo at 12:00 UTC today",
                owner="person_005",
                due="2026-08-26T12:00:00Z",
            )
        ],
        "tags": ["positive", "pronoun"],
    },
    {
        "text": (
            "I will reserve the east meeting room by 2026-08-28 12:00 UTC "
            "for September 2 at 19:00 UTC."
        ),
        "items": [
            commitment(
                "resource_reservation",
                "Reserve the east meeting room",
                "I will reserve the east meeting room by 2026-08-28 12:00 UTC",
                owner="person_006",
                due="2026-08-28T12:00:00Z",
            )
        ],
        "tags": ["positive", "reservation"],
    },
    {
        "text": "I will book the community hall by 2026-08-29 14:00 UTC.",
        "items": [
            commitment(
                "resource_reservation",
                "Book the community hall",
                "I will book the community hall by 2026-08-29 14:00 UTC",
                owner="person_007",
                due="2026-08-29T14:00:00Z",
            )
        ],
        "tags": ["positive", "reservation"],
    },
    {
        "text": ("<@person_008> will reserve two library tables by 2026-09-02 12:00 UTC."),
        "items": [
            commitment(
                "resource_reservation",
                "Reserve two library tables",
                "<@person_008> will reserve two library tables by 2026-09-02 12:00 UTC",
                owner="person_008",
                due="2026-09-02T12:00:00Z",
            )
        ],
        "tags": ["positive", "third-person"],
    },
    {
        "text": "I will request the courtyard permit by 2026-08-26 17:00 UTC.",
        "items": [
            commitment(
                "resource_reservation",
                "Request the courtyard permit",
                "I will request the courtyard permit by 2026-08-26 17:00 UTC",
                owner="person_009",
                due="2026-08-26T17:00:00Z",
            )
        ],
        "tags": ["positive", "permit"],
    },
    {
        "text": "I'll hold the Zoom room for our Sunday check-in.",
        "items": [
            commitment(
                "resource_reservation",
                "Reserve the Zoom room for the Sunday check-in",
                "I'll hold the Zoom room for our Sunday check-in",
                owner="person_010",
            )
        ],
        "tags": ["positive", "online-resource"],
    },
    {
        "text": "I will buy 30 name tags by 2026-08-28 17:00 UTC and keep the receipt.",
        "items": [
            commitment(
                "purchase",
                "Buy 30 name tags and keep the receipt",
                "I will buy 30 name tags by 2026-08-28 17:00 UTC and keep the receipt",
                owner="person_011",
                due="2026-08-28T17:00:00Z",
            )
        ],
        "tags": ["positive", "purchase"],
    },
    {
        "text": (
            "<@person_012> agreed to order the tea within the 40 dollar cap by "
            "2026-08-26 23:00 UTC."
        ),
        "items": [
            commitment(
                "purchase",
                "Order tea within the 40 dollar cap",
                (
                    "<@person_012> agreed to order the tea within the 40 dollar cap by "
                    "2026-08-26 23:00 UTC"
                ),
                owner="person_012",
                due="2026-08-26T23:00:00Z",
            )
        ],
        "tags": ["positive", "money"],
    },
    {
        "text": "I'll replace the broken cable after I confirm the model number.",
        "items": [
            commitment(
                "purchase",
                "Replace the broken cable after confirming its model number",
                "I'll replace the broken cable after I confirm the model number",
                owner="person_013",
            )
        ],
        "tags": ["positive", "dependency"],
    },
    {
        "text": "<@person_014> will pick up compostable cups on the way over.",
        "items": [
            commitment(
                "purchase",
                "Pick up compostable cups",
                "<@person_014> will pick up compostable cups on the way over",
                owner="person_014",
            )
        ],
        "tags": ["positive", "third-person"],
    },
    {
        "text": (
            "I will purchase the replacement lock by 2026-08-27 18:00 UTC "
            "if the price stays under 25 dollars."
        ),
        "items": [
            commitment(
                "purchase",
                "Purchase the replacement lock if it costs under 25 dollars",
                (
                    "I will purchase the replacement lock by 2026-08-27 18:00 UTC "
                    "if the price stays under 25 dollars"
                ),
                owner="person_015",
                due="2026-08-27T18:00:00Z",
            )
        ],
        "tags": ["positive", "conditional"],
    },
    {
        "text": "I will upload the final attendee list by 2026-08-27 16:00 UTC.",
        "items": [
            commitment(
                "information_submission",
                "Upload the final attendee list",
                "I will upload the final attendee list by 2026-08-27 16:00 UTC",
                owner="person_016",
                due="2026-08-27T16:00:00Z",
            )
        ],
        "tags": ["positive", "submission"],
    },
    {
        "text": (
            "<@person_017> will add the accessibility notes to the shared document by "
            "2026-08-26 23:00 UTC."
        ),
        "items": [
            commitment(
                "information_submission",
                "Add accessibility notes to the shared document",
                (
                    "<@person_017> will add the accessibility notes to the shared document by "
                    "2026-08-26 23:00 UTC"
                ),
                owner="person_017",
                due="2026-08-26T23:00:00Z",
            )
        ],
        "tags": ["positive", "document"],
    },
    {
        "text": "I'll send the headcount spreadsheet by 2026-08-27 12:00 UTC.",
        "items": [
            commitment(
                "information_submission",
                "Send the headcount spreadsheet",
                "I'll send the headcount spreadsheet by 2026-08-27 12:00 UTC",
                owner="person_018",
                due="2026-08-27T12:00:00Z",
            )
        ],
        "tags": ["positive", "spreadsheet"],
    },
    {
        "text": (
            "<@person_019> owns the safety checklist and will post it by 2026-08-31 17:00 UTC."
        ),
        "items": [
            commitment(
                "information_submission",
                "Post the safety checklist",
                (
                    "<@person_019> owns the safety checklist and will post it by "
                    "2026-08-31 17:00 UTC"
                ),
                owner="person_019",
                due="2026-08-31T17:00:00Z",
            )
        ],
        "tags": ["positive", "ownership"],
    },
    {
        "text": "I will add the venue address and transit directions to the event page.",
        "items": [
            commitment(
                "information_submission",
                "Add the venue address and transit directions to the event page",
                "I will add the venue address and transit directions to the event page",
                owner="person_020",
            )
        ],
        "tags": ["positive", "content-update"],
    },
    {
        "text": (
            "I'll email the library manager for written confirmation by 2026-08-27 17:00 UTC."
        ),
        "items": [
            commitment(
                "external_communication",
                "Email the library manager for written confirmation",
                ("I'll email the library manager for written confirmation by 2026-08-27 17:00 UTC"),
                owner="person_021",
                due="2026-08-27T17:00:00Z",
            )
        ],
        "tags": ["positive", "email"],
    },
    {
        "text": (
            "<@person_022> will call the supplier at 2026-08-27 10:00 UTC and report back here."
        ),
        "items": [
            commitment(
                "external_communication",
                "Call the supplier and report back",
                (
                    "<@person_022> will call the supplier at 2026-08-27 10:00 UTC "
                    "and report back here"
                ),
                owner="person_022",
                due="2026-08-27T10:00:00Z",
            )
        ],
        "tags": ["positive", "phone"],
    },
    {
        "text": "I will notify the building desk that our group arrives at six.",
        "items": [
            commitment(
                "external_communication",
                "Notify the building desk that the group arrives at six",
                "I will notify the building desk that our group arrives at six",
                owner="person_023",
            )
        ],
        "tags": ["positive", "notification"],
    },
    {
        "text": (
            "<@person_024> agreed to ask the photographer about image consent by "
            "2026-08-26 18:00 UTC."
        ),
        "items": [
            commitment(
                "external_communication",
                "Ask the photographer about image consent",
                (
                    "<@person_024> agreed to ask the photographer about image consent by "
                    "2026-08-26 18:00 UTC"
                ),
                owner="person_024",
                due="2026-08-26T18:00:00Z",
            )
        ],
        "tags": ["positive", "consent"],
    },
    {
        "text": "I'll reply to the neighborhood association with our final number.",
        "items": [
            commitment(
                "external_communication",
                "Reply to the neighborhood association with the final number",
                "I'll reply to the neighborhood association with our final number",
                owner="person_025",
            )
        ],
        "tags": ["positive", "reply"],
    },
    {
        "text": "We decided on the indoor plan; I will publish that decision now.",
        "items": [
            commitment(
                "event_decision",
                "Publish the decision to use the indoor plan",
                "I will publish that decision now",
                owner="person_026",
            )
        ],
        "tags": ["positive", "decision"],
    },
    {
        "text": ("The group approved 6 PM, and <@person_027> will lock that start time in."),
        "items": [
            commitment(
                "event_decision",
                "Set the event start time to 6 PM",
                "<@person_027> will lock that start time in",
                owner="person_027",
            )
        ],
        "tags": ["positive", "approved-choice"],
    },
    {
        "text": (
            "I will close the poll at 2026-08-27 12:00 UTC and use the top option "
            "unless there is a tie."
        ),
        "items": [
            commitment(
                "event_decision",
                "Close the poll and select the top option unless tied",
                (
                    "I will close the poll at 2026-08-27 12:00 UTC and use the top option "
                    "unless there is a tie"
                ),
                owner="person_028",
                due="2026-08-27T12:00:00Z",
            )
        ],
        "tags": ["positive", "timeout-default"],
    },
    {
        "text": ("<@person_029> will record the approved rain-date choice in the event page."),
        "items": [
            commitment(
                "event_decision",
                "Record the approved rain-date choice",
                "<@person_029> will record the approved rain-date choice in the event page",
                owner="person_029",
            )
        ],
        "tags": ["positive", "decision-record"],
    },
    {
        "text": "We'll use the smaller room. I will update the public event listing.",
        "items": [
            commitment(
                "event_decision",
                "Use the smaller room and update the public event listing",
                "I will update the public event listing",
                owner="person_030",
            )
        ],
        "tags": ["positive", "decision-publication"],
    },
    {
        "text": (
            "I will bring the keys and upload the access instructions by 2026-08-28 17:00 UTC."
        ),
        "items": [
            commitment(
                "item_handoff",
                "Bring the keys",
                "I will bring the keys",
                owner="person_031",
                due="2026-08-28T17:00:00Z",
            ),
            commitment(
                "information_submission",
                "Upload the access instructions",
                "upload the access instructions by 2026-08-28 17:00 UTC",
                owner="person_031",
                due="2026-08-28T17:00:00Z",
            ),
        ],
        "tags": ["positive", "multi-commitment"],
    },
    {
        "text": (
            "<@person_099> will reserve the room, and I will email the confirmation to the group."
        ),
        "items": [
            commitment(
                "resource_reservation",
                "Reserve the room",
                "<@person_099> will reserve the room",
                owner="person_099",
            ),
            commitment(
                "external_communication",
                "Email the reservation confirmation to the group",
                "I will email the confirmation to the group",
                owner="person_032",
            ),
        ],
        "tags": ["positive", "multi-owner"],
    },
    {
        "text": (
            "I will buy the labels and deliver them to the front desk by 2026-08-27 17:00 UTC."
        ),
        "items": [
            commitment(
                "purchase",
                "Buy the labels",
                "I will buy the labels",
                owner="person_033",
                due="2026-08-27T17:00:00Z",
            ),
            commitment(
                "item_handoff",
                "Deliver the labels to the front desk",
                "deliver them to the front desk by 2026-08-27 17:00 UTC",
                owner="person_033",
                due="2026-08-27T17:00:00Z",
            ),
        ],
        "tags": ["positive", "multi-commitment"],
    },
    {
        "text": ("Update cmt_seed_034: I will deliver the labels by 2026-08-28 17:00 UTC instead."),
        "items": [
            commitment(
                "item_handoff",
                "Deliver the labels on Friday",
                "I will deliver the labels by 2026-08-28 17:00 UTC instead",
                owner="person_034",
                due="2026-08-28T17:00:00Z",
                operation="update",
                target="cmt_seed_034",
            )
        ],
        "tags": ["mutation", "update"],
    },
    {
        "text": "Cancel cmt_seed_035: I am no longer buying the extra markers.",
        "items": [
            commitment(
                "purchase",
                "Cancel the extra marker purchase",
                "I am no longer buying the extra markers",
                owner="person_035",
                operation="cancel",
                target="cmt_seed_035",
            )
        ],
        "tags": ["mutation", "cancel"],
    },
    {
        "text": ("Update cmt_seed_036: <@person_036>, not <@person_099>, will contact the venue."),
        "items": [
            commitment(
                "external_communication",
                "Contact the venue",
                "<@person_036>, not <@person_099>, will contact the venue",
                owner="person_036",
                operation="update",
                target="cmt_seed_036",
            )
        ],
        "tags": ["mutation", "owner-correction"],
    },
    {"text": "What if we met outside instead?", "items": [], "tags": ["negative", "question"]},
    {
        "text": "Thanks, that sounds good to me.",
        "items": [],
        "tags": ["negative", "acknowledgement"],
    },
    {
        "text": "The room looked crowded last time.",
        "items": [],
        "tags": ["negative", "observation"],
    },
    {
        "text": "Maybe someone could bring a spare cable.",
        "items": [],
        "tags": ["negative", "suggestion"],
    },
    {"text": "I wish the form were shorter.", "items": [], "tags": ["negative", "wish"]},
    {
        "text": "The tea has already been ordered and delivered.",
        "items": [],
        "tags": ["negative", "completed-status"],
    },
    {
        "text": "Can Ava email the venue?",
        "items": [],
        "tags": ["negative", "request-without-acceptance"],
    },
    {"text": "We talked about using the library.", "items": [], "tags": ["negative", "discussion"]},
    {
        "text": "I might buy cups if nobody else does.",
        "items": [],
        "tags": ["negative", "weak-intent"],
    },
    {"text": "The poll currently favors Saturday.", "items": [], "tags": ["negative", "status"]},
    {
        "text": "I'll take care of it tomorrow.",
        "items": [],
        "ambiguity": {
            "field": "intent",
            "reason": "The task referred to by it is absent from this message.",
        },
        "tags": ["ambiguity", "missing-task"],
    },
    {
        "text": "One of us will send the form tonight.",
        "items": [],
        "ambiguity": {"field": "owner_id", "reason": "The responsible person is not identified."},
        "tags": ["ambiguity", "missing-owner"],
    },
    {
        "text": "I will bring the box after the meeting.",
        "items": [],
        "ambiguity": {
            "field": "due_at",
            "reason": "The referenced meeting time is not present in this message.",
        },
        "tags": ["ambiguity", "unresolved-time"],
    },
    {
        "text": "Cancel that reservation; we do not need it.",
        "items": [],
        "ambiguity": {
            "field": "target_commitment_id",
            "reason": "No reservation commitment identifier is present.",
        },
        "tags": ["ambiguity", "missing-target"],
    },
]


def build_case(index: int, spec: dict[str, Any]) -> dict[str, Any]:
    case_id = f"gold_{index:03d}"
    source_ref = f"slack:C_SYN:{BASE_TS + index}.000100"
    event = {
        "schema_version": "1.0",
        "organization_id": "org_synthetic",
        "channel_id": "C_SYN",
        "message_id": f"msg_{index:03d}",
        "actor_id": f"person_{index:03d}",
        "occurred_at": f"2026-08-26T{(index - 1) % 24:02d}:00:00Z",
        "text": spec["text"],
        "data_classification": "synthetic",
        "source": {
            "provider": "slack",
            "workspace_id": "workspace_synthetic",
            "source_message_ref": source_ref,
        },
    }
    items = []
    for raw_item in spec["items"]:
        item = dict(raw_item)
        quote = item.pop("evidence_quote")
        item["evidence"] = {
            "source_message_ref": source_ref,
            "evidence_quote": quote,
        }
        items.append(item)
    ambiguities = []
    if "ambiguity" in spec:
        ambiguities.append({**spec["ambiguity"], "source_message_ref": source_ref})
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "data_classification": "synthetic",
        "event": event,
        "expected": {"commitments": items, "ambiguities": ambiguities},
        "tags": spec["tags"],
    }


def main() -> int:
    if len(CASES) != 50:
        raise ValueError(f"expected 50 cases, found {len(CASES)}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        json.dumps(build_case(index, spec), ensure_ascii=False, sort_keys=True)
        for index, spec in enumerate(CASES, start=1)
    )
    OUTPUT.write_text(content + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} synthetic cases to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
