# AgentCore Memory and Gateway Evidence — September 1, 2026

This record documents a real, short-lived AWS verification of AgentCore Memory and AgentCore
Gateway without claiming a continuously hosted service, a model-backed memory result, or an
executed external tool. It contains no credentials, prompts, personal data, or provider payloads.

## Reproducible evidence

- **AWS Region:** `ap-northeast-1`
- **Source commit:** `a9ee8abe27b4bd5a313dfecc688112195963133c`
- **GitHub Actions run:**
  <https://github.com/wellkilo/Quorum/actions/runs/33469765620>
- **Authentication:** GitHub OIDC with short-lived credentials for
  `QuorumAgentCoreDeployerRole`; the workflow rejected any other caller identity.
- **Artifact:** Python 3.13 Linux arm64 Gateway Lambda CodeZip, 50,114,522 compressed bytes and
  5,768 files.
- **Lambda safety gate:** `lambda_gate=execution-disabled`.
- **Memory lifecycle:** reached `ACTIVE` with `QuorumFacts` and `QuorumSummaries`;
  `events_created=0`.
- **Gateway lifecycle:** reached `READY` with `AWS_IAM` authentication. Signed MCP initialization
  and `tools/list` returned exactly `calendar_create_tentative_event`, `gmail_create_draft`, and
  `forms_create_response_request`; `tool_calls=0`.
- **Cleanup:** both the primary lifecycle and the independent idempotent cleanup reported
  `cleanup=complete`. A separate control-plane check found no remaining Quorum Memory, Gateway,
  temporary Lambda, Lambda log group, or verification bucket.

## Cost and side-effect boundary

The workflow fixed `QUORUM_BEDROCK_ENABLED=false` and `QUORUM_EXECUTION_ENABLED=false`. It created no
Memory events, made no Gateway `tools/call` request, and did not invoke a Bedrock model. The direct
Lambda safety check failed closed before PostgreSQL, Slack, or Google Workspace clients could be
initialized. Both the Runtime and Gateway Lambda roles independently deny Bedrock model invocation.

The workflow created only uniquely named, short-lived verification resources. A `finally` path and a
separate GitHub Actions `always()` step both invoked cleanup, and repeated deletion was handled
idempotently. Resource permissions are restricted by region and Quorum resource-name prefixes so
cleanup remains possible while a resource transitions through deletion states.

## What this proves — and what it does not

This run proves that the reviewed repository can build the real Gateway Lambda artifact, create an
AgentCore Memory with the intended strategy namespaces, create an IAM-authenticated AgentCore
Gateway and Lambda target, discover exactly the three typed MCP tools through SigV4, enforce the
execution-disabled gate, and clean up every temporary resource.

It does not prove model-backed Memory extraction or retrieval quality, Gateway tool execution, a
Google Workspace or Slack side effect, a continuously hosted backend, managed OpenTelemetry trace
capture, a live PostgreSQL integration, or real-world community impact. The public anonymous demo
remains the visibly synthetic GitHub Pages replay.
