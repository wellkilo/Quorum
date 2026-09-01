# Quorum External API Contract

Status: phase-four executable contracts. The HTTP application and its routes are locally tested;
no public deployment or credentialed external-service result is claimed.

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
- Deduplicate by the canonical opaque message identity and source reference in the business store.
- Never persist the token, signature, or raw message in application logs.
- Convert accepted events into the canonical event below before entering the Graph.

Success response:

```json
{
  "accepted": true,
  "event_id": "message_opaque"
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

## 2.1 Slack outbound interaction contract

Quorum has exactly three outbound Slack interactions:

1. one `chat.postMessage` group receipt with optional Open and Undo URL buttons;
2. one `conversations.open(users=<one participant>)` call followed by one private
   `chat.postMessage` decision question;
3. one weekly `chat.postMessage` with six compact fields: closed decisions, decision-latency P50,
   total interruptions, people interrupted, maximum interrupts per person, and undo rate.

`WeeklySummary` rejects a weekly interruption total that cannot fit within the declared number of
people and the default two-interrupt-per-person budget. Synthetic smoke messages begin with
`Synthetic demo` and the weekly context states that they are not a measured real-world outcome.
`quorum-slack-smoke` previews by default; only `--confirm-live-posts` performs the three Web API
writes. The command makes zero model calls and zero execution-tool calls.

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

## 5. Deterministic action request

This is the typed input to the Risk Appraiser and Quorum Router. The three risk dimensions are
declared values, not free-form model output. Candidate deciders are ordered and unique.

```json
{
  "schema_version": "1.0",
  "action_id": "action_opaque",
  "organization_id": "org_opaque",
  "requested_by_id": "person_requester",
  "action_class": "event_decision",
  "tool_name": "calendar_create_tentative_event",
  "summary": "Create a tentative planning event",
  "reversibility": "reversible",
  "impact_radius": "individual",
  "money_impact": "none",
  "candidate_decider_ids": ["person_a", "person_b"],
  "action_arguments": {
    "title": "Tentative planning event",
    "starts_at": "2026-08-28T09:00:00+08:00",
    "ends_at": "2026-08-28T10:00:00+08:00",
    "time_zone": "Asia/Shanghai",
    "receipt_channel_id": "C123"
  },
  "requested_at": "2026-08-26T10:00:00Z"
}
```

Allowed risk values are:

- `reversibility`: `reversible`, `compensatable`, or `irreversible`;
- `impact_radius`: `individual`, `group`, or `external`;
- `money_impact`: `none`, `budgeted`, or `unbudgeted`.

## 6. Persisted policy decision

`DecisionPolicyStore.decide()` is idempotent on `(organization_id, action_id)`. Reusing the same
identity with different request content is a conflict. The method locks the autonomy profile and
candidate budget accounts, computes spend in the same transaction, and appends one `requested`
interrupt event for each selected decider.

```json
{
  "action_id": "action_opaque",
  "organization_id": "org_opaque",
  "requested_by_id": "person_requester",
  "action_class": "event_decision",
  "tool_name": "calendar_create_tentative_event",
  "arguments_fingerprint": "64-lowercase-hex-characters",
  "risk": {
    "score": 0,
    "tier": "low",
    "reversibility_points": 0,
    "impact_radius_points": 0,
    "money_impact_points": 0,
    "reasons": [
      "reversibility:reversible=0",
      "impact_radius:individual=0",
      "money_impact:none=0"
    ]
  },
  "autonomy": {
    "level": 0,
    "consecutive_approvals": 0,
    "rejection_count": 0,
    "undo_count": 0
  },
  "required_quorum": 1,
  "selected_decider_ids": ["person_a"],
  "budgets": [{"participant_id": "person_a", "spent": 0, "limit": 2}],
  "status": "awaiting_approval",
  "timeout_at": "2026-08-27T10:00:00Z",
  "timeout_default": "execute_and_notify"
}
```

Decision statuses are `authorized`, `awaiting_approval`, `deferred_budget`, `approved`, `rejected`,
`expired`, `executed`, and `undone`. Interrupt responses may only come from selected deciders and may
not be changed. Repeated identical responses are idempotent. Three completed approvals promote one
autonomy level; rejection or a valid undo lowers one level. A response received at or after the
persisted timeout is not accepted: the timeout default is applied first. Undo accepts only authorized,
approved, or executed reversible actions and is itself idempotent.

## 7. Native hook interrupt contract

The Executor passes `organization_id` and `action_id` in the tool input.
`QuorumAutonomyGate` re-reads the persisted decision, verifies the exact tool name and canonical
SHA-256 fingerprint of every other argument, and creates one Strands interrupt per selected quorum
member. Optional top-level null values are omitted during canonicalization.

```json
{
  "name": "quorum-approval-0",
  "reason": {
    "action_id": "action_opaque",
    "participant_id": "person_a",
    "risk_tier": "low",
    "required_quorum": 1,
    "timeout_at": "2026-08-27T10:00:00Z",
    "timeout_default": "execute_and_notify"
  }
}
```

Accepted response payloads are the strings `approve`, `approved`, `yes`, or `y`, or an object whose
`decision` contains one of those values. Any other response is a rejection. Missing policy, a tool
name or argument mismatch, or a non-executable status cancels the tool call.

## 8. Reversible executor tools

The implemented Strands tool names are:

- `calendar_create_tentative_event`: Calendar v3 `events.insert`, with `status=tentative` and
  `sendUpdates=none`; undo calls `events.delete`.
- `gmail_create_draft`: Gmail v1 `users.drafts.create`; it never calls a send endpoint; undo calls
  `users.drafts.delete`.
- `forms_create_response_request`: Forms v1 `forms.create` plus `forms.batchUpdate`; undo deletes the
  created file through Drive v3 `files.delete`.

Every typed tool input requires `organization_id` and `action_id`. Provider-specific fields must
exactly match the arguments fingerprint approved by the policy layer. All three tools reject unknown
fields and irreversible policy decisions.

## 9. Execution receipt

`ActionExecutionService` returns this internal typed boundary after a successful provider call:

```json
{
  "organization_id": "org_opaque",
  "action_id": "action_opaque",
  "tool_name": "calendar_create_tentative_event",
  "provider": "google_calendar",
  "external_resource_id": "opaque-provider-id",
  "external_url": "https://provider.example/opaque-resource",
  "status": "executed",
  "reversible": true,
  "executed_at": "2026-08-27T09:00:00Z",
  "undo_expires_at": "2026-08-28T09:00:00Z",
  "undo_url": "https://demo.example/actions/undo?token=redacted"
}
```

An exact retry returns the persisted receipt without repeating the provider call. A transport error,
5xx response, invalid response, or missing provider resource ID is recorded as `uncertain` and is not
automatically retried. The database stores no action arguments or undo token plaintext.

## 10. AgentCore Runtime invocation

`POST /invocations` is hosted by AgentCore Runtime through `BedrockAgentCoreApp`.

Required session header:

```http
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <opaque-session-id>
```

Request body:

```json
{
  "organization_id": "org_opaque",
  "prompt": "Create the already-approved tentative event.",
  "data_classification": "synthetic",
  "action_request": {
    "schema_version": "1.0",
    "action_id": "action_opaque",
    "organization_id": "org_opaque",
    "requested_by_id": "person_requester",
    "action_class": "event_decision",
    "tool_name": "calendar_create_tentative_event",
    "summary": "Create a tentative planning event",
    "reversibility": "reversible",
    "impact_radius": "individual",
    "money_impact": "none",
    "candidate_decider_ids": ["person_a"],
    "action_arguments": {},
    "requested_at": "2026-08-26T10:00:00Z"
  },
  "interrupt_responses": []
}
```

Response body:

```json
{
  "session_id": "opaque-session-id-at-least-33-characters",
  "status": "completed|interrupted|failed",
  "execution_order": ["listener", "ledger_curator"],
  "interrupts": [],
  "usage": {}
}
```

The session ID is required, 33–256 characters, starts with an alphanumeric character, and contains
only alphanumerics, hyphens, or underscores. The invocation and action-request organization IDs must
match.

Model access is an operator-controlled deployment setting, never a request field. Production starts
with `QUORUM_BEDROCK_ENABLED=false`. While disabled, a valid request returns HTTP `503` before
AgentCore Memory, Gateway, or Bedrock is initialized:

```json
{
  "error": "Bedrock model calls are disabled; set QUORUM_BEDROCK_ENABLED=true only for a controlled run"
}
```

When an operator deliberately enables a bounded run, `QUORUM_BEDROCK_MAX_TOKENS` limits each model
response to 64–1024 output tokens and defaults to 384. This application guard is not an AWS billing
hard limit.

## 11. AgentCore Memory verification contract

The manual `Verify AgentCore Memory and Gateway` workflow creates a uniquely named, short-lived
Memory resource with the following control-plane request. It waits for `ACTIVE`, verifies the two
strategy names, creates zero events, and deletes the resource before the job ends.

```json
{
  "name": "QuorumMemory<workflow-run-id>",
  "description": "Short-lived Quorum strategy verification with zero events",
  "eventExpiryDuration": 7,
  "memoryStrategies": [
    {"semanticMemoryStrategy": {"name": "QuorumFacts", "namespaceTemplates": ["/facts/{actorId}/"]}},
    {"summaryMemoryStrategy": {"name": "QuorumSummaries", "namespaceTemplates": ["/summaries/{actorId}/{sessionId}/"]}}
  ],
  "tags": {
    "Project": "Quorum",
    "DataClassification": "SyntheticOnly",
    "CostMode": "ZeroModel"
  }
}
```

This proves the managed Memory resource and namespace contract only. It does not claim a model-backed
memory extraction result, because the cost-safe verification writes zero events.

The contract was exercised in `ap-northeast-1` on September 1, 2026. The public run reported
`memory_status=ACTIVE`, both configured strategy names, `events_created=0`, and complete cleanup. See
the [evidence record](docs/evidence/agentcore-services-2026-09-01.md) and
[GitHub Actions run](https://github.com/wellkilo/Quorum/actions/runs/33469765620).

## 12. AgentCore Gateway verification contract

The same workflow creates an IAM-authenticated MCP Gateway and a Lambda target. The target contains
the exact schemas returned by `gateway_tool_definitions()`:

```json
{
  "gatewayIdentifier": "<gateway-id>",
  "name": "quorum-execution",
  "targetConfiguration": {
    "mcp": {
      "lambda": {
        "lambdaArn": "<short-lived-lambda-arn>",
        "toolSchema": {
          "inlinePayload": [
            {"name": "calendar_create_tentative_event", "inputSchema": {}},
            {"name": "gmail_create_draft", "inputSchema": {}},
            {"name": "forms_create_response_request", "inputSchema": {}}
          ]
        }
      }
    }
  },
  "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
}
```

The verifier waits for the Gateway and target to reach `READY`, uses a SigV4-authenticated MCP client
to run initialization and `tools/list`, and requires exactly the three names above. It deliberately
runs zero `tools/call` requests. The Lambda also defaults `QUORUM_EXECUTION_ENABLED=false`; an
attempted invocation fails before database, Slack, or Google clients are initialized.

The September 1 public run reported `lambda_gate=execution-disabled`,
`gateway_status=READY`, `authentication=AWS_IAM`, the exact three tool names, `tool_calls=0`, and
complete cleanup. This is control-plane and tool-discovery evidence, not execution evidence.

## 13. Human interrupt resume

The Strands interrupt response is sent back to the same logical session.

```json
{
  "organization_id": "org_opaque",
  "prompt": "Resume the approved action.",
  "data_classification": "synthetic",
  "action_request": {"...": "the identical original action request"},
  "interrupt_responses": [
    {
      "interrupt_id": "interrupt_opaque",
      "response": "approve"
    }
  ]
}
```

## 14. Undo action

`GET` is deliberately non-mutating so Slack link previews and security scanners cannot consume a
single-use token:

```http
GET /actions/undo?token=<single-use-signed-token>
```

It returns an HTML confirmation form. The response sets `Cache-Control: no-store` and
`Referrer-Policy: no-referrer` so the single-use token is not cached or forwarded as a referrer. The
user-confirmed mutation is:

```http
POST /actions/undo
Content-Type: application/x-www-form-urlencoded

token=<single-use-signed-token>
```

Success response:

```json
{
  "action_id": "action_opaque",
  "status": "undone",
  "undone_at": "2026-08-26T10:05:00Z"
}
```

The token is HMAC-SHA256 signed, scoped to one organization and action, expires after 24 hours, and
is excluded from logs. Only its SHA-256 digest is persisted. It is atomically consumed before the
provider delete call; tampered, expired, or reused tokens fail closed. Provider undo failure remains
auditable as `undo_failed` and does not silently restore the token.

## 15. Replay API for the public sandbox

`POST /demo/replays/synthetic-week`

The request body is empty.

Every replay response must state its provenance:

```json
{
  "replay_id": "replay_opaque",
  "dataset_id": "synthetic_week_v1",
  "data_classification": "synthetic",
  "baseline": {"message_count": 214, "closed_decisions": 3},
  "quorum": {"interruption_count": 6, "closed_decisions": 6},
  "disclaimer": "Synthetic demonstration data; not a measured real-world outcome."
}
```

The full snapshot is retrieved with `GET /demo/metrics/{replay_id}`. Unknown replay IDs return 404.

The public GitHub Pages build cannot expose a Python API. It reads the versioned
`synthetic-week.json` artifact with the same response shape and an explicit static replay ID. An
executable test compares every non-ID field with `ReplayStore`; the page explicitly states that this
is static synthetic evidence and not a deployed AgentCore result.

## 16. Error envelope

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
