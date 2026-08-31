#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${AWS_REGION:?Set AWS_REGION}"

AWS_CLI="${AWS_CLI:-aws}"
RUNTIME_NAME="${QUORUM_AGENTCORE_RUNTIME_NAME:-QuorumRuntime}"
BUCKET_NAME="${QUORUM_AGENTCORE_BUCKET:-quorum-agentcore-code-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
OBJECT_KEY="${QUORUM_AGENTCORE_OBJECT_KEY:-runtime/quorum-agentcore-runtime.zip}"

runtime_id="$("${AWS_CLI}" bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
  --output text)"
if [[ -n "${runtime_id}" && "${runtime_id}" != "None" ]]; then
  "${AWS_CLI}" bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "${runtime_id}"
fi

if "${AWS_CLI}" s3api head-bucket \
  --bucket "${BUCKET_NAME}" \
  --expected-bucket-owner "${AWS_ACCOUNT_ID}" >/dev/null 2>&1; then
  "${AWS_CLI}" s3api delete-object \
    --bucket "${BUCKET_NAME}" \
    --key "${OBJECT_KEY}" \
    --expected-bucket-owner "${AWS_ACCOUNT_ID}" >/dev/null
  "${AWS_CLI}" s3api delete-bucket \
    --bucket "${BUCKET_NAME}" \
    --expected-bucket-owner "${AWS_ACCOUNT_ID}"
fi
