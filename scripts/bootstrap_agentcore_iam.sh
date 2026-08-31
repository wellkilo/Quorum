#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${AWS_REGION:?Set AWS_REGION}"

AWS_CLI="${AWS_CLI:-aws}"
RUNTIME_NAME="${QUORUM_AGENTCORE_RUNTIME_NAME:-QuorumRuntime}"
BUCKET_NAME="${QUORUM_AGENTCORE_BUCKET:-quorum-agentcore-code-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
OBJECT_KEY="${QUORUM_AGENTCORE_OBJECT_KEY:-runtime/quorum-agentcore-runtime.zip}"
DEPLOYER_ROLE="QuorumAgentCoreDeployerRole"
RUNTIME_ROLE="QuorumAgentCoreRuntimeRole"
OIDC_PROVIDER_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
RUNTIME_IDENTITY_SLR="AWSServiceRoleForBedrockAgentCoreRuntimeIdentity"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

jq -n --arg provider "${OIDC_PROVIDER_ARN}" '{
  Version: "2012-10-17",
  Statement: [{
    Sid: "AllowQuorumMainViaGitHubOIDC",
    Effect: "Allow",
    Principal: {Federated: $provider},
    Action: "sts:AssumeRoleWithWebIdentity",
    Condition: {StringEquals: {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": [
        "repo:wellkilo/Quorum:ref:refs/heads/main",
        "repo:wellkilo@150929960/Quorum@1346855429:ref:refs/heads/main"
      ]
    }}
  }]
}' >"${work_dir}/deployer-trust.json"

jq -n \
  --arg account "${AWS_ACCOUNT_ID}" \
  --arg region "${AWS_REGION}" \
  --arg runtime_role "${RUNTIME_ROLE}" \
  --arg bucket "${BUCKET_NAME}" \
  --arg object_key "${OBJECT_KEY}" \
  --arg runtime_name "${RUNTIME_NAME}" \
  '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "ListAgentRuntimesInTokyo",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore:ListAgentRuntimeVersions",
          "bedrock-agentcore:ListAgentRuntimeEndpoints"
        ],
        Resource: "*",
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "CreateTaggedQuorumRuntimeInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:CreateAgentRuntime",
        Resource: "*",
        Condition: {StringEquals: {
          "aws:RequestedRegion": $region,
          "aws:RequestTag/Project": "Quorum",
          "aws:RequestTag/CostMode": "ZeroModel"
        }}
      },
      {
        Sid: "CreateDefaultRuntimeEndpointInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:CreateAgentRuntimeEndpoint",
        Resource: ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "ManageRuntimeWorkloadIdentityInTokyo",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:CreateWorkloadIdentity",
          "bedrock-agentcore:GetWorkloadIdentity",
          "bedrock-agentcore:DeleteWorkloadIdentity"
        ],
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/" + $runtime_name + "-*")
        ],
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "TagNewQuorumRuntimeInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:TagResource",
        Resource: ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/*"),
        Condition: {
          StringEquals: {
            "aws:RequestedRegion": $region,
            "aws:RequestTag/Project": "Quorum",
            "aws:RequestTag/DataClassification": "SyntheticOnly",
            "aws:RequestTag/CostMode": "ZeroModel"
          },
          "ForAllValues:StringEquals": {
            "aws:TagKeys": ["Project", "DataClassification", "CostMode"]
          }
        }
      },
      {
        Sid: "ReadTagsForQuorumRuntimeResourcesInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:ListTagsForResource",
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/" + $runtime_name + "-*"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/" + $runtime_name + "-*/runtime-endpoint/*"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/" + $runtime_name + "-*")
        ],
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "ManageOnlyQuorumRuntime",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:GetAgentRuntime",
          "bedrock-agentcore:UpdateAgentRuntime",
          "bedrock-agentcore:DeleteAgentRuntime",
          "bedrock-agentcore:GetAgentRuntimeEndpoint",
          "bedrock-agentcore:UpdateAgentRuntimeEndpoint",
          "bedrock-agentcore:DeleteAgentRuntimeEndpoint"
        ],
        Resource: ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/" + $runtime_name + "-*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "InvokeQuorumRuntimeForVerification",
        Effect: "Allow",
        Action: ["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:StopRuntimeSession"],
        Resource: ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/" + $runtime_name + "-*")
      },
      {
        Sid: "PassOnlyQuorumRuntimeRole",
        Effect: "Allow",
        Action: "iam:PassRole",
        Resource: ("arn:aws:iam::" + $account + ":role/" + $runtime_role),
        Condition: {StringEquals: {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}
      },
      {
        Sid: "ReadQuorumRuntimeLogs",
        Effect: "Allow",
        Action: ["logs:DescribeLogGroups", "logs:DescribeLogStreams", "logs:GetLogEvents"],
        Resource: [
          ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/*"),
          ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*")
        ]
      },
      {
        Sid: "CreatePrivateArtifactBucket",
        Effect: "Allow",
        Action: "s3:CreateBucket",
        Resource: ("arn:aws:s3:::" + $bucket)
      },
      {
        Sid: "ConfigurePrivateArtifactBucket",
        Effect: "Allow",
        Action: [
          "s3:GetBucketLocation",
          "s3:GetBucketPublicAccessBlock",
          "s3:PutBucketPublicAccessBlock",
          "s3:GetEncryptionConfiguration",
          "s3:PutEncryptionConfiguration",
          "s3:GetLifecycleConfiguration",
          "s3:PutLifecycleConfiguration",
          "s3:ListBucket",
          "s3:DeleteBucket"
        ],
        Resource: ("arn:aws:s3:::" + $bucket)
      },
      {
        Sid: "ManageOnlyQuorumArtifact",
        Effect: "Allow",
        Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        Resource: ("arn:aws:s3:::" + $bucket + "/" + $object_key)
      }
    ]
  }' >"${work_dir}/deployer-policy.json"

jq -n --arg account "${AWS_ACCOUNT_ID}" --arg region "${AWS_REGION}" '{
  Version: "2012-10-17",
  Statement: [{
    Sid: "AllowAgentCoreRuntime",
    Effect: "Allow",
    Principal: {Service: "bedrock-agentcore.amazonaws.com"},
    Action: "sts:AssumeRole",
    Condition: {
      StringEquals: {"aws:SourceAccount": $account},
      ArnLike: {"aws:SourceArn": ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":*")}
    }
  }]
}' >"${work_dir}/runtime-trust.json"

jq -n \
  --arg account "${AWS_ACCOUNT_ID}" \
  --arg region "${AWS_REGION}" \
  --arg bucket "${BUCKET_NAME}" \
  --arg object_key "${OBJECT_KEY}" \
  '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "ReadDeploymentArtifact",
        Effect: "Allow",
        Action: ["s3:GetObject", "s3:GetObjectVersion"],
        Resource: ("arn:aws:s3:::" + $bucket + "/" + $object_key)
      },
      {
        Sid: "RuntimeLogGroup",
        Effect: "Allow",
        Action: ["logs:CreateLogGroup", "logs:DescribeLogGroups", "logs:DescribeLogStreams"],
        Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/*")
      },
      {
        Sid: "RuntimeLogEvents",
        Effect: "Allow",
        Action: ["logs:CreateLogStream", "logs:PutLogEvents"],
        Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*")
      },
      {
        Sid: "RuntimeTelemetry",
        Effect: "Allow",
        Action: [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets"
        ],
        Resource: "*"
      },
      {
        Sid: "RuntimeMetrics",
        Effect: "Allow",
        Action: "cloudwatch:PutMetricData",
        Resource: "*",
        Condition: {StringEquals: {"cloudwatch:namespace": "bedrock-agentcore"}}
      },
      {
        Sid: "BlockAllModelInference",
        Effect: "Deny",
        Action: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        Resource: "*"
      }
    ]
  }' >"${work_dir}/runtime-policy.json"

if ! "${AWS_CLI}" iam get-open-id-connect-provider \
  --open-id-connect-provider-arn "${OIDC_PROVIDER_ARN}" >/dev/null 2>&1; then
  "${AWS_CLI}" iam create-open-id-connect-provider \
    --url "https://token.actions.githubusercontent.com" \
    --client-id-list "sts.amazonaws.com" \
    --tags Key=Project,Value=Quorum >/dev/null
fi

if ! "${AWS_CLI}" iam get-role --role-name "${RUNTIME_IDENTITY_SLR}" >/dev/null 2>&1; then
  "${AWS_CLI}" iam create-service-linked-role \
    --aws-service-name runtime-identity.bedrock-agentcore.amazonaws.com >/dev/null
fi

if ! "${AWS_CLI}" iam get-role --role-name "${DEPLOYER_ROLE}" >/dev/null 2>&1; then
  "${AWS_CLI}" iam create-role \
    --role-name "${DEPLOYER_ROLE}" \
    --description "Least-privilege Quorum AgentCore CodeZip deployer" \
    --assume-role-policy-document "file://${work_dir}/deployer-trust.json" \
    --tags Key=Project,Value=Quorum Key=CostMode,Value=ZeroModel >/dev/null
fi
"${AWS_CLI}" iam update-assume-role-policy \
  --role-name "${DEPLOYER_ROLE}" \
  --policy-document "file://${work_dir}/deployer-trust.json"
"${AWS_CLI}" iam put-role-policy \
  --role-name "${DEPLOYER_ROLE}" \
  --policy-name QuorumAgentCoreDeployPolicy \
  --policy-document "file://${work_dir}/deployer-policy.json"

if ! "${AWS_CLI}" iam get-role --role-name "${RUNTIME_ROLE}" >/dev/null 2>&1; then
  "${AWS_CLI}" iam create-role \
    --role-name "${RUNTIME_ROLE}" \
    --description "Quorum AgentCore Runtime role with model inference denied" \
    --assume-role-policy-document "file://${work_dir}/runtime-trust.json" \
    --tags Key=Project,Value=Quorum Key=CostMode,Value=ZeroModel >/dev/null
fi
"${AWS_CLI}" iam update-assume-role-policy \
  --role-name "${RUNTIME_ROLE}" \
  --policy-document "file://${work_dir}/runtime-trust.json"
"${AWS_CLI}" iam put-role-policy \
  --role-name "${RUNTIME_ROLE}" \
  --policy-name QuorumAgentCoreRuntimePolicy \
  --policy-document "file://${work_dir}/runtime-policy.json"

printf 'deployer_role_arn=arn:aws:iam::%s:role/%s\n' "${AWS_ACCOUNT_ID}" "${DEPLOYER_ROLE}"
printf 'runtime_role_arn=arn:aws:iam::%s:role/%s\n' "${AWS_ACCOUNT_ID}" "${RUNTIME_ROLE}"
