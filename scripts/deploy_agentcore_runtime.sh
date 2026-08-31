#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${AWS_REGION:?Set AWS_REGION}"
: "${QUORUM_AGENTCORE_ARTIFACT:?Set QUORUM_AGENTCORE_ARTIFACT}"

AWS_CLI="${AWS_CLI:-aws}"
RUNTIME_NAME="${QUORUM_AGENTCORE_RUNTIME_NAME:-QuorumRuntime}"
BUCKET_NAME="${QUORUM_AGENTCORE_BUCKET:-quorum-agentcore-code-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
OBJECT_KEY="${QUORUM_AGENTCORE_OBJECT_KEY:-runtime/quorum-agentcore-runtime.zip}"
RUNTIME_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/QuorumAgentCoreRuntimeRole"

if [[ "${QUORUM_BEDROCK_ENABLED:-false}" != "false" ]]; then
  echo "QUORUM_BEDROCK_ENABLED must remain false for the zero-model deployment." >&2
  exit 2
fi

if ! "${AWS_CLI}" s3api head-bucket \
  --bucket "${BUCKET_NAME}" \
  --expected-bucket-owner "${AWS_ACCOUNT_ID}" >/dev/null 2>&1; then
  if [[ "${AWS_REGION}" == "us-east-1" ]]; then
    "${AWS_CLI}" s3api create-bucket --bucket "${BUCKET_NAME}" >/dev/null
  else
    "${AWS_CLI}" s3api create-bucket \
      --bucket "${BUCKET_NAME}" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
  fi
fi
"${AWS_CLI}" s3api put-public-access-block \
  --bucket "${BUCKET_NAME}" \
  --public-access-block-configuration \
  'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
"${AWS_CLI}" s3api put-bucket-encryption \
  --bucket "${BUCKET_NAME}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":false}]}'
"${AWS_CLI}" s3api put-bucket-lifecycle-configuration \
  --bucket "${BUCKET_NAME}" \
  --lifecycle-configuration \
  '{"Rules":[{"ID":"ExpireQuorumArtifact","Status":"Enabled","Filter":{"Prefix":"runtime/"},"Expiration":{"Days":1}}]}'
"${AWS_CLI}" s3api put-object \
  --bucket "${BUCKET_NAME}" \
  --key "${OBJECT_KEY}" \
  --body "${QUORUM_AGENTCORE_ARTIFACT}" \
  --expected-bucket-owner "${AWS_ACCOUNT_ID}" >/dev/null

request_file="$(mktemp)"
trap 'rm -f "$request_file"' EXIT
jq -n \
  --arg name "${RUNTIME_NAME}" \
  --arg bucket "${BUCKET_NAME}" \
  --arg key "${OBJECT_KEY}" \
  --arg role "${RUNTIME_ROLE_ARN}" \
  --arg region "${AWS_REGION}" \
  '{
    agentRuntimeName: $name,
    description: "Quorum zero-model synthetic replay runtime",
    agentRuntimeArtifact: {codeConfiguration: {
      code: {s3: {bucket: $bucket, prefix: $key}},
      runtime: "PYTHON_3_13",
      entryPoint: ["main.py"]
    }},
    roleArn: $role,
    networkConfiguration: {networkMode: "PUBLIC"},
    lifecycleConfiguration: {idleRuntimeSessionTimeout: 60, maxLifetime: 900},
    environmentVariables: {
      QUORUM_BEDROCK_ENABLED: "false",
      QUORUM_BEDROCK_MAX_TOKENS: "384",
      QUORUM_AWS_REGION: $region,
      QUORUM_DATABASE_URL: "sqlite+pysqlite:////tmp/quorum.sqlite3"
    },
    tags: {Project: "Quorum", DataClassification: "SyntheticOnly", CostMode: "ZeroModel"}
  }' >"${request_file}"

runtime_id="$("${AWS_CLI}" bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
  --output text)"
if [[ -n "${runtime_id}" && "${runtime_id}" != "None" ]]; then
  jq --arg runtime_id "${runtime_id}" \
    '{
      agentRuntimeId: $runtime_id,
      agentRuntimeArtifact,
      roleArn,
      networkConfiguration,
      description,
      lifecycleConfiguration,
      environmentVariables
    }' "${request_file}" >"${request_file}.update"
  "${AWS_CLI}" bedrock-agentcore-control update-agent-runtime \
    --cli-input-json "file://${request_file}.update"
else
  "${AWS_CLI}" bedrock-agentcore-control create-agent-runtime \
    --cli-input-json "file://${request_file}"
fi
