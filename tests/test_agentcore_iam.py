from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPOSITORY_ROOT / "scripts/bootstrap_agentcore_iam.sh"
DEPLOY_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/deploy-agentcore.yml"
SERVICES_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/verify-agentcore-services.yml"


class AgentCoreIamBootstrapTest(unittest.TestCase):
    def test_managed_identity_create_uses_agentcore_placeholder_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            captured_policy = root / "deployer-policy.json"
            fake_aws = root / "aws"
            fake_aws.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "iam put-role-policy" ]]; then
  role_name=""
  policy_document=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --role-name) role_name="$2"; shift 2 ;;
      --policy-document) policy_document="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ "$role_name" == "QuorumAgentCoreDeployerRole" ]]; then
    cp "${policy_document#file://}" "$CAPTURED_POLICY"
  fi
fi
""",
                encoding="utf-8",
            )
            fake_aws.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "AWS_ACCOUNT_ID": "123456789012",
                    "AWS_REGION": "ap-northeast-1",
                    "AWS_CLI": str(fake_aws),
                    "CAPTURED_POLICY": str(captured_policy),
                }
            )

            subprocess.run(
                ["bash", str(BOOTSTRAP_SCRIPT)],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )

            policy = json.loads(captured_policy.read_text(encoding="utf-8"))
            statements = {statement["Sid"]: statement for statement in policy["Statement"]}
            create = statements["CreateManagedRuntimeWorkloadIdentityInTokyo"]
            read = statements["ReadNamedQuorumWorkloadIdentityInTokyo"]
            delete = statements["DeleteManagedRuntimeWorkloadIdentityInTokyo"]
            tag = statements["TagNewQuorumRuntimeInTokyo"]

            self.assertEqual(create["Action"], "bedrock-agentcore:CreateWorkloadIdentity")
            self.assertEqual(
                create["Resource"],
                [
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default",
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default/workload-identity/*",
                ],
            )
            self.assertEqual(
                create["Condition"]["StringEquals"]["aws:RequestedRegion"],
                "ap-northeast-1",
            )
            self.assertEqual(
                set(read["Resource"]),
                {
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default/workload-identity/QuorumRuntime-*",
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default/workload-identity/quorumgateway-*",
                },
            )
            self.assertEqual(
                set(delete["Resource"]),
                {
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default",
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default/workload-identity/QuorumRuntime-*",
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default/workload-identity/quorumgateway-*",
                },
            )
            self.assertEqual(
                tag["Resource"],
                [
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/*",
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default",
                    "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:"
                    "workload-identity-directory/default/workload-identity/*",
                ],
            )
            self.assertEqual(
                tag["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"],
                ["Project", "DataClassification", "CostMode"],
            )
            create_services = statements["CreateShortLivedQuorumMemoryAndGateway"]
            self.assertEqual(
                set(create_services["Action"]),
                {"bedrock-agentcore:CreateMemory", "bedrock-agentcore:CreateGateway"},
            )
            self.assertEqual(
                create_services["Condition"]["StringEquals"]["aws:RequestTag/CostMode"],
                "ZeroModel",
            )
            memory = statements["ManageShortLivedQuorumMemory"]
            self.assertEqual(
                memory["Resource"],
                "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:memory/QuorumMemory*",
            )
            self.assertEqual(
                memory["Condition"]["StringEquals"],
                {"aws:RequestedRegion": "ap-northeast-1"},
            )
            gateway = statements["ManageShortLivedQuorumGateway"]
            self.assertIn("bedrock-agentcore:InvokeGateway", gateway["Action"])
            self.assertEqual(
                gateway["Resource"],
                "arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:gateway/quorumgateway-*",
            )
            self.assertEqual(
                gateway["Condition"]["StringEquals"],
                {"aws:RequestedRegion": "ap-northeast-1"},
            )
            lambda_statement = statements["ManageShortLivedGatewayLambda"]
            self.assertEqual(
                lambda_statement["Resource"],
                "arn:aws:lambda:ap-northeast-1:123456789012:function:QuorumExecutionTools-*",
            )
            self.assertEqual(
                statements["DeleteShortLivedGatewayLambdaLogs"]["Action"],
                "logs:DeleteLogGroup",
            )

    def test_deployment_requires_remote_503_evidence_and_always_cleans_up(self) -> None:
        workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("Invocation returned HTTP 503", workflow)
        self.assertIn("contains(logStreamName, '$session_id')", workflow)
        self.assertIn('if [[ "$invoke_exit" == "0" ]]', workflow)
        self.assertIn(
            "always() && inputs.operation == 'deploy'",
            workflow,
        )

    def test_services_workflow_is_zero_model_zero_event_zero_tool_call_and_cleans_up(self) -> None:
        workflow = SERVICES_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('QUORUM_BEDROCK_ENABLED: "false"', workflow)
        self.assertIn('QUORUM_EXECUTION_ENABLED: "false"', workflow)
        self.assertIn("verify_agentcore_services.py verify", workflow)
        self.assertIn("Emergency cleanup after an incomplete verification", workflow)
        self.assertIn("always() && inputs.operation == 'verify'", workflow)


if __name__ == "__main__":
    unittest.main()
