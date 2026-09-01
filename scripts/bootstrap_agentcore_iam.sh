#!/usr/bin/env bash
set -euo pipefail

: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${AWS_REGION:?Set AWS_REGION}"

AWS_CLI="${AWS_CLI:-aws}"
RUNTIME_NAME="${QUORUM_AGENTCORE_RUNTIME_NAME:-QuorumRuntime}"
BUCKET_NAME="${QUORUM_AGENTCORE_BUCKET:-quorum-agentcore-code-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
VERIFY_BUCKET_NAME="${QUORUM_AGENTCORE_VERIFY_BUCKET:-quorum-agentcore-verify-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
OBJECT_KEY="${QUORUM_AGENTCORE_OBJECT_KEY:-runtime/quorum-agentcore-runtime.zip}"
VERIFY_OBJECT_KEY="${QUORUM_AGENTCORE_VERIFY_OBJECT_KEY:-verification/quorum-gateway-lambda.zip}"
DEPLOYER_ROLE="QuorumAgentCoreDeployerRole"
RUNTIME_ROLE="QuorumAgentCoreRuntimeRole"
GATEWAY_ROLE="QuorumAgentCoreGatewayRole"
LAMBDA_ROLE="QuorumGatewayLambdaRole"
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
  --arg verify_bucket "${VERIFY_BUCKET_NAME}" \
  --arg object_key "${OBJECT_KEY}" \
  --arg verify_object_key "${VERIFY_OBJECT_KEY}" \
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
        Sid: "CreateManagedRuntimeWorkloadIdentityInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:CreateWorkloadIdentity",
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/*")
        ],
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "ReadNamedQuorumWorkloadIdentityInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:GetWorkloadIdentity",
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/" + $runtime_name + "-*"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/quorumgateway-*")
        ],
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "DeleteManagedRuntimeWorkloadIdentityInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:DeleteWorkloadIdentity",
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/" + $runtime_name + "-*"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/quorumgateway-*")
        ],
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "TagNewQuorumRuntimeInTokyo",
        Effect: "Allow",
        Action: "bedrock-agentcore:TagResource",
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":runtime/*"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":workload-identity-directory/default/workload-identity/*")
        ],
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
        Action: [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:FilterLogEvents",
          "logs:GetLogEvents"
        ],
        Resource: [
          ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/*"),
          ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*")
        ]
      },
      {
        Sid: "DeleteOnlyQuorumRuntimeEvidenceLogs",
        Effect: "Allow",
        Action: "logs:DeleteLogGroup",
        Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/" + $runtime_name + "-*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "ManageTemporaryTransactionSearchForQuorum",
        Effect: "Allow",
        Action: [
          "logs:DeleteResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:PutResourcePolicy",
          "xray:GetIndexingRules",
          "xray:GetTraceSegmentDestination",
          "xray:UpdateIndexingRule",
          "xray:UpdateTraceSegmentDestination"
        ],
        Resource: "*",
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "CreateShortLivedQuorumMemoryAndGateway",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:CreateMemory",
          "bedrock-agentcore:CreateGateway"
        ],
        Resource: "*",
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
        Sid: "ListAgentCoreVerificationResources",
        Effect: "Allow",
        Action: ["bedrock-agentcore:ListMemories", "bedrock-agentcore:ListGateways"],
        Resource: "*",
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "ManageShortLivedQuorumMemory",
        Effect: "Allow",
        Action: ["bedrock-agentcore:GetMemory", "bedrock-agentcore:DeleteMemory"],
        Resource: ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":memory/QuorumMemory*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "ManageShortLivedQuorumGateway",
        Effect: "Allow",
        Action: [
          "bedrock-agentcore:GetGateway",
          "bedrock-agentcore:DeleteGateway",
          "bedrock-agentcore:CreateGatewayTarget",
          "bedrock-agentcore:GetGatewayTarget",
          "bedrock-agentcore:DeleteGatewayTarget",
          "bedrock-agentcore:ListGatewayTargets",
          "bedrock-agentcore:InvokeGateway"
        ],
        Resource: ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":gateway/quorumgateway-*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "TagShortLivedQuorumResources",
        Effect: "Allow",
        Action: "bedrock-agentcore:TagResource",
        Resource: [
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":memory/*"),
          ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":gateway/*")
        ],
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
        Sid: "ManageShortLivedGatewayLambda",
        Effect: "Allow",
        Action: [
          "lambda:CreateFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:InvokeFunction",
          "lambda:DeleteFunction"
        ],
        Resource: ("arn:aws:lambda:" + $region + ":" + $account + ":function:QuorumExecutionTools-*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "TagShortLivedGatewayLambda",
        Effect: "Allow",
        Action: "lambda:TagResource",
        Resource: ("arn:aws:lambda:" + $region + ":" + $account + ":function:QuorumExecutionTools-*"),
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
        Sid: "DeleteShortLivedGatewayLambdaLogs",
        Effect: "Allow",
        Action: "logs:DeleteLogGroup",
        Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/lambda/QuorumExecutionTools-*:*"),
        Condition: {StringEquals: {"aws:RequestedRegion": $region}}
      },
      {
        Sid: "PassOnlyQuorumGatewayRoleToAgentCore",
        Effect: "Allow",
        Action: "iam:PassRole",
        Resource: ("arn:aws:iam::" + $account + ":role/QuorumAgentCoreGatewayRole"),
        Condition: {StringEquals: {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}
      },
      {
        Sid: "PassOnlyQuorumLambdaRoleToLambda",
        Effect: "Allow",
        Action: "iam:PassRole",
        Resource: ("arn:aws:iam::" + $account + ":role/QuorumGatewayLambdaRole"),
        Condition: {StringEquals: {"iam:PassedToService": "lambda.amazonaws.com"}}
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
      },
      {
        Sid: "CreatePrivateVerificationBucket",
        Effect: "Allow",
        Action: "s3:CreateBucket",
        Resource: ("arn:aws:s3:::" + $verify_bucket)
      },
      {
        Sid: "ConfigurePrivateVerificationBucket",
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
        Resource: ("arn:aws:s3:::" + $verify_bucket)
      },
      {
        Sid: "ManageOnlyQuorumVerificationArtifact",
        Effect: "Allow",
        Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        Resource: ("arn:aws:s3:::" + $verify_bucket + "/" + $verify_object_key)
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
  --arg runtime_name "${RUNTIME_NAME}" \
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
        Sid: "RuntimeUnifiedTracePolicy",
        Effect: "Allow",
        Action: "logs:PutResourcePolicy",
        Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/bedrock-agentcore/runtimes/" + $runtime_name + "-*")
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

jq -n --arg account "${AWS_ACCOUNT_ID}" --arg region "${AWS_REGION}" '{
  Version: "2012-10-17",
  Statement: [{
    Effect: "Allow",
    Principal: {Service: "bedrock-agentcore.amazonaws.com"},
    Action: "sts:AssumeRole",
    Condition: {
      StringEquals: {"aws:SourceAccount": $account},
      ArnLike: {"aws:SourceArn": ("arn:aws:bedrock-agentcore:" + $region + ":" + $account + ":gateway/*")}
    }
  }]
}' >"${work_dir}/gateway-trust.json"

jq -n --arg account "${AWS_ACCOUNT_ID}" --arg region "${AWS_REGION}" '{
  Version: "2012-10-17",
  Statement: [{
    Sid: "InvokeOnlyQuorumGatewayLambda",
    Effect: "Allow",
    Action: "lambda:InvokeFunction",
    Resource: ("arn:aws:lambda:" + $region + ":" + $account + ":function:QuorumExecutionTools-*")
  }]
}' >"${work_dir}/gateway-policy.json"

jq -n '{
  Version: "2012-10-17",
  Statement: [{
    Effect: "Allow",
    Principal: {Service: "lambda.amazonaws.com"},
    Action: "sts:AssumeRole"
  }]
}' >"${work_dir}/lambda-trust.json"

jq -n --arg account "${AWS_ACCOUNT_ID}" --arg region "${AWS_REGION}" '{
  Version: "2012-10-17",
  Statement: [
    {
      Sid: "CreateOnlyOwnLambdaLogGroup",
      Effect: "Allow",
      Action: "logs:CreateLogGroup",
      Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/lambda/QuorumExecutionTools-*")
    },
    {
      Sid: "WriteOnlyOwnLambdaLogStreams",
      Effect: "Allow",
      Action: ["logs:CreateLogStream", "logs:PutLogEvents"],
      Resource: ("arn:aws:logs:" + $region + ":" + $account + ":log-group:/aws/lambda/QuorumExecutionTools-*:*")
    },
    {
      Sid: "BlockAllModelInference",
      Effect: "Deny",
      Action: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      Resource: "*"
    }
  ]
}' >"${work_dir}/lambda-policy.json"

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

if ! "${AWS_CLI}" iam get-role --role-name "${GATEWAY_ROLE}" >/dev/null 2>&1; then
  "${AWS_CLI}" iam create-role \
    --role-name "${GATEWAY_ROLE}" \
    --description "Quorum AgentCore Gateway role limited to its short-lived Lambda target" \
    --assume-role-policy-document "file://${work_dir}/gateway-trust.json" \
    --tags Key=Project,Value=Quorum Key=CostMode,Value=ZeroModel >/dev/null
fi
"${AWS_CLI}" iam update-assume-role-policy \
  --role-name "${GATEWAY_ROLE}" \
  --policy-document "file://${work_dir}/gateway-trust.json"
"${AWS_CLI}" iam put-role-policy \
  --role-name "${GATEWAY_ROLE}" \
  --policy-name QuorumGatewayInvokePolicy \
  --policy-document "file://${work_dir}/gateway-policy.json"

if ! "${AWS_CLI}" iam get-role --role-name "${LAMBDA_ROLE}" >/dev/null 2>&1; then
  "${AWS_CLI}" iam create-role \
    --role-name "${LAMBDA_ROLE}" \
    --description "Quorum short-lived Gateway Lambda role with model inference denied" \
    --assume-role-policy-document "file://${work_dir}/lambda-trust.json" \
    --tags Key=Project,Value=Quorum Key=CostMode,Value=ZeroModel >/dev/null
fi
"${AWS_CLI}" iam update-assume-role-policy \
  --role-name "${LAMBDA_ROLE}" \
  --policy-document "file://${work_dir}/lambda-trust.json"
"${AWS_CLI}" iam put-role-policy \
  --role-name "${LAMBDA_ROLE}" \
  --policy-name QuorumGatewayLambdaPolicy \
  --policy-document "file://${work_dir}/lambda-policy.json"

printf 'deployer_role_arn=arn:aws:iam::%s:role/%s\n' "${AWS_ACCOUNT_ID}" "${DEPLOYER_ROLE}"
printf 'runtime_role_arn=arn:aws:iam::%s:role/%s\n' "${AWS_ACCOUNT_ID}" "${RUNTIME_ROLE}"
printf 'gateway_role_arn=arn:aws:iam::%s:role/%s\n' "${AWS_ACCOUNT_ID}" "${GATEWAY_ROLE}"
printf 'lambda_role_arn=arn:aws:iam::%s:role/%s\n' "${AWS_ACCOUNT_ID}" "${LAMBDA_ROLE}"
