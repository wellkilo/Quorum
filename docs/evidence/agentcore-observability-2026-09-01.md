# AgentCore Managed Observability Evidence — September 1, 2026

This record documents one real, short-lived AWS verification of a managed OpenTelemetry span from
Amazon Bedrock AgentCore Runtime. The invocation used a strictly typed synthetic probe and opened no
model, Memory, Gateway tool, Slack, Google Workspace, or business-persistence path. This is
infrastructure and privacy-boundary evidence, not a production trace or a real-world outcome.

## Reproducible evidence

- **AWS Region:** `ap-northeast-1`
- **Source commit:** `7ab510f8d56ce34d03602f1ed02d6cb9680d05c1`
- **GitHub Actions run:**
  <https://github.com/wellkilo/Quorum/actions/runs/33507672504>
- **Job:** `runtime` (`99855529555`), completed successfully.
- **Authentication:** separate GitHub OIDC sessions with short-lived, scoped Runtime and
  observability credentials.
- **Runtime:** short-lived `QuorumRuntime-TDLinV3Dvr`, created for the evidence window and removed
  after verification.
- **Managed span:** `quorum.observability.probe`.
- **Trace ID:** `6a96c6220c745b4a0741b4b47a693da1`.
- **Matched span count:** `1`.
- **Classification:** `data_classification=synthetic`.
- **Privacy scan:** `forbidden_content_matches=0`.

The probe response returned its trace and span identifiers. The workflow then searched the managed
span stream, required exactly one matching span, compared the identifiers, and rejected any captured
privacy sentinel or forbidden downstream-service marker.

## Zero-call and fail-closed boundary

The successful job reported:

```text
model_calls=0
memory_events=0
gateway_tool_calls=0
external_side_effect_calls=0
```

`QUORUM_BEDROCK_ENABLED=false` and `QUORUM_EXECUTION_ENABLED=false` remained fixed. A separate
invocation of the production model path returned the expected HTTP `503` with AWS CLI exit `254`,
proving that the cost gate still failed closed after the observability probe. No prompt, raw message,
tool argument, provider payload, name, email address, or phone number was added to the span.

## Temporary configuration and cleanup

Transaction Search was changed from `XRay` to `CloudWatchLogs` only for the verification window, with
indexing held at `0.0%`. The workflow created one uniquely named resource policy and later reported:

```text
transaction_search_destination_restored=XRay
transaction_search_indexing_restored=0.0
transaction_search_policy_removed=true
temporary_managed_log_groups_removed=2
service_managed_application_signals_channels_retained=0
temporary_application_signals_role_removed=true
```

The `service_managed_application_signals_channels_retained=0` value means that no new Application
Signals channel existed relative to the workflow's pre-run snapshot. It does **not** claim that the
workflow deleted an AWS service-managed channel: AWS exposes that deletion only to the owning
service. An account-level Application Signals service channel that predated this verification remains
an AWS-managed, zero-data control-plane resource.

After the run, direct control-plane reads confirmed that Transaction Search was `ACTIVE` on the prior
`XRay` destination at `0.0%` indexing, the temporary resource policy was absent, both temporary
managed log groups were absent, the temporary Application Signals service-linked role was absent,
and the Runtime and artifact bucket were absent.

## What this proves — and what it does not

This run proves that the reviewed Runtime artifact can emit one PII-safe managed OpenTelemetry span,
that the repository can locate and validate that span through the AWS-managed path, that the disabled
model path remains fail-closed, and that the temporary customer-managed resources are restored or
removed automatically.

It does not prove Bedrock model quality, model-backed Memory extraction or retrieval, Gateway
`tools/call` execution, a Slack or Google Workspace side effect, a continuously hosted backend, a
live PostgreSQL integration, or real-world community impact. The public anonymous demo remains the
visibly synthetic GitHub Pages replay.
