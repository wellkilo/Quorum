#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${AWS_REGION:?Set AWS_REGION}"

AWS_CLI="${AWS_CLI:-aws}"
RUNTIME_NAME="${QUORUM_AGENTCORE_RUNTIME_NAME:-QuorumRuntime}"
BUCKET_NAME="${QUORUM_AGENTCORE_BUCKET:-quorum-agentcore-code-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
OBJECT_KEY="${QUORUM_AGENTCORE_OBJECT_KEY:-runtime/quorum-agentcore-runtime.zip}"

runtime_id="${QUORUM_AGENTCORE_RUNTIME_ID:-}"
if [[ -z "${runtime_id}" ]]; then
  runtime_id="$("${AWS_CLI}" bedrock-agentcore-control list-agent-runtimes \
    --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
    --output text)"
fi
if [[ -n "${runtime_id}" && "${runtime_id}" != "None" ]]; then
  "${AWS_CLI}" bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "${runtime_id}"
  if [[ "${QUORUM_DELETE_RUNTIME_LOG_GROUP:-false}" == "true" ]]; then
    log_group="/aws/bedrock-agentcore/runtimes/${runtime_id}-DEFAULT"
    for attempt in $(seq 1 12); do
      if ! "${AWS_CLI}" logs describe-log-groups \
        --log-group-name-prefix "${log_group}" \
        --query "logGroups[?logGroupName=='${log_group}'].logGroupName | [0]" \
        --output text | grep -Fxq "${log_group}"; then
        break
      fi
      if "${AWS_CLI}" logs delete-log-group --log-group-name "${log_group}" >/dev/null 2>&1; then
        break
      fi
      if [[ "${attempt}" == "12" ]]; then
        echo "Runtime log group cleanup did not complete: ${log_group}" >&2
        exit 1
      fi
      sleep 5
    done
    if "${AWS_CLI}" logs describe-log-groups \
      --log-group-name-prefix "${log_group}" \
      --query "logGroups[?logGroupName=='${log_group}'].logGroupName | [0]" \
      --output text | grep -Fxq "${log_group}"; then
      echo "Runtime log group still exists after cleanup: ${log_group}" >&2
      exit 1
    fi
    printf 'runtime_log_group_removed=%s\n' "${log_group}"
  fi
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
