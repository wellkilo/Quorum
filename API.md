# Quorum External API Contract

Status: design contract; endpoints are not yet claimed as deployed.

## 1. Slack Events API ingress

`POST /integrations/slack/events`

Required request headers:

```http
Content-Type: application/json
X-Slack-Request-Timestamp: 1770000000
X-Slack-Signature: v0=<hex-hmac-sha256>
```

Representative Slack event body:

```json
{
  "token": "not persisted",
  "team_id": "T123",
  "api_app_id": "A123",
  "type": "event_callback",
  "event_id": "Ev123",
  "event_time": 1770000000,
  "event": {
    "type": "message",
    "channel": "C123",
    "user": "U123",
    "text": "I can bring the keys on Friday",
    "ts": "1770000000.000100"
  }
}
```

Rules:

- Verify Slack's `v0:{timestamp}:{rawBody}` HMAC-SHA256 signature before JSON parsing.
- Reject timestamps outside the configured replay window.
- Deduplicate by `event_id`.
- Never persist the token, signature, or raw message in application logs.
- Convert accepted events into the canonical event below before entering the Graph.

Success response:

```json
{
  "accepted": true,
  "event_id": "Ev123",
  "trace_id": "opaque-trace-id"
}
```

## 2. Canonical message event

This is an internal typed boundary, not a public HTTP endpoint.

```json
{
  "schema_version": "1.0",
  "organization_id": "org_opaque",
  "channel_id": "channel_opaque",
  "message_id": "message_opaque",
  "actor_id": "person_opaque",
  "occurred_at": "2026-08-26T10:00:00Z",
  "text": "I can bring the keys on Friday",
  "data_classification": "redacted-real",
  "source": {
    "provider": "slack",
    "workspace_id": "workspace_opaque",
    "source_message_ref": "slack:C123:1770000000.000100"
  }
}
```

Allowed `data_classification` values are `synthetic` and `redacted-real`. Raw or unredacted data is
not a valid Graph input.

## 3. Ledger extraction output

The Ledger Curator returns a typed `ExtractionEnvelope`. A candidate is rejected before storage if
its source reference differs from the current event or its evidence quote is not present verbatim
in the event text.

```json
{
  "commitments": [
    {
      "operation": "create",
      "task_class": "item_handoff",
      "summary": "Bring the storage keys",
      "owner_id": "person_opaque",
      "due_at": "2026-08-28T17:00:00Z",
      "target_commitment_id": null,
      "confidence": 0.96,
      "evidence": {
        "source_message_ref": "slack:C123:1770000000.000100",
        "evidence_quote": "I will bring the storage keys"
      }
    }
  ],
  "ambiguities": []
}
```

## 4. Commitment Ledger persistence result

This is an internal typed boundary returned by `DatabaseLedger.apply()`, not a public HTTP endpoint.
The call executes one transaction containing the processed-message idempotency claim, all accepted
commitment changes, and their audit events.

```json
{
  "upserted": [
    {
      "commitment_id": "cmt_opaque",
      "organization_id": "org_opaque",
      "task_class": "item_handoff",
      "summary": "Bring the storage keys",
      "owner_id": "person_opaque",
      "due_at": "2026-08-28T17:00:00Z",
      "status": "open",
      "source_message_refs": ["slack:C123:1770000000.000100"],
      "created_at": "2026-08-26T10:00:01Z",
      "updated_at": "2026-08-26T10:00:01Z",
      "confidence": 0.96
    }
  ],
  "rejected": [],
  "duplicate_event": false
}
```

Retry contract:

- an exact retry of the same organization and source-message reference returns
  `duplicate_event: true` without writing another commitment or audit event;
- reusing that identity with different canonical content raises an idempotency conflict;
- rejected candidates return a stable rejection code and are never persisted;
- an exception rolls back the message claim, commitment rows, and audit events together.

The production database URL is supplied only through `QUORUM_DATABASE_URL`. Supported schemes are
`postgresql+psycopg://` for production and `sqlite+pysqlite://` for local development. All reads and
mutations require `organization_id`; this storage layer never accepts raw Slack payloads.

## 5. AgentCore Runtime invocation

`POST /invocations` is hosted by AgentCore Runtime through `BedrockAgentCoreApp`.

Required session header:

```http
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <opaque-session-id>
```

Request body:

```json
{
  "schema_version": "1.0",
  "operation": "process_message",
  "event": {
    "message_id": "message_opaque",
    "organization_id": "org_opaque",
    "text": "redacted text",
    "source_message_ref": "slack:C123:1770000000.000100"
  }
}
```

Response body:

```json
{
  "status": "completed|interrupted|rejected",
  "trace_id": "opaque-trace-id",
  "ledger_changes": [],
  "interrupts": [],
  "receipts": []
}
```

## 6. Human interrupt resume

The Strands interrupt response is sent back to the same logical session.

```json
{
  "schema_version": "1.0",
  "operation": "resume_interrupt",
  "responses": [
    {
      "interruptResponse": {
        "interruptId": "interrupt_opaque",
        "response": {
          "decision": "approve|reject|edit",
          "selected_option": "option_opaque"
        }
      }
    }
  ]
}
```

## 7. Undo action

`POST /actions/{action_id}/undo`

```json
{
  "undo_token": "single-use-signed-token",
  "requested_by": "person_opaque"
}
```

Response:

```json
{
  "action_id": "action_opaque",
  "status": "undone|already_undone|expired|not_reversible",
  "undone_at": "2026-08-26T10:05:00Z"
}
```

The token must be short-lived, single-use, scoped to one action, and excluded from logs.

## 8. Replay API for the public sandbox

`POST /demo/replays/synthetic-week`

```json
{
  "dataset_id": "synthetic_week_v1",
  "speed": "fast",
  "reset": true
}
```

Every replay response must state its provenance:

```json
{
  "run_id": "run_opaque",
  "data_classification": "synthetic",
  "status": "running",
  "metrics_url": "/demo/runs/run_opaque/metrics"
}
```

## 9. Error envelope

```json
{
  "error": {
    "code": "INVALID_SIGNATURE",
    "message": "The request could not be authenticated.",
    "retryable": false,
    "trace_id": "opaque-trace-id"
  }
}
```

Error messages must never echo raw message content or credentials.
