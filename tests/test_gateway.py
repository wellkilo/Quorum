from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from quorum.execution import ActionExecutionService
from quorum.gateway import gateway_lambda_handler, gateway_tool_definitions, provision_gateway
from quorum.models import FormActionInput


class GatewayContractTest(unittest.TestCase):
    def test_forms_schema_preserves_nested_question_contract(self) -> None:
        definitions = {item["name"]: item for item in gateway_tool_definitions()}

        self.assertEqual(
            set(definitions),
            {
                "calendar_create_tentative_event",
                "gmail_create_draft",
                "forms_create_response_request",
            },
        )
        questions = definitions["forms_create_response_request"]["inputSchema"]["properties"][
            "questions"
        ]
        self.assertEqual(questions["type"], "array")
        self.assertEqual(questions["items"]["type"], "object")
        self.assertEqual(
            questions["items"]["properties"],
            {"title": {"type": "string"}, "required": {"type": "boolean"}},
        )
        self.assertEqual(questions["items"]["required"], ["title"])

    def test_lambda_dispatches_flat_gateway_arguments_to_typed_service(self) -> None:
        service = MagicMock(spec=ActionExecutionService)
        receipt = MagicMock()
        receipt.model_dump.return_value = {"status": "executed"}
        service.create_response_request.return_value = receipt
        context = SimpleNamespace(
            client_context=SimpleNamespace(
                custom={
                    "bedrockAgentCoreToolName": "quorum-execution___forms_create_response_request"
                }
            )
        )
        event = {
            "organization_id": "org_opaque",
            "action_id": "action_opaque",
            "title": "Availability",
            "questions": [{"title": "Can you attend?", "required": True}],
        }

        with patch("quorum.gateway._get_lambda_service", return_value=service):
            result = gateway_lambda_handler(event, context)

        self.assertEqual(result, {"status": "executed"})
        action = service.create_response_request.call_args.args[0]
        self.assertIsInstance(action, FormActionInput)
        self.assertEqual(action.questions[0].title, "Can you attend?")
        receipt.model_dump.assert_called_once_with(mode="json")

    def test_lambda_rejects_unknown_tool_before_loading_credentials(self) -> None:
        context = SimpleNamespace(
            client_context=SimpleNamespace(
                custom={"bedrockAgentCoreToolName": "target___unrestricted_execute"}
            )
        )

        with (
            patch("quorum.gateway._get_lambda_service") as service,
            self.assertRaisesRegex(ValueError, "unsupported Gateway tool"),
        ):
            gateway_lambda_handler({}, context)

        service.assert_not_called()

    def test_provisioning_uses_iam_gateway_and_inline_lambda_schema(self) -> None:
        client = MagicMock()
        client.create_gateway.return_value = {
            "gatewayId": "gw-123",
            "gatewayUrl": "https://gateway.example/mcp",
        }
        client.create_gateway_target.return_value = {"targetId": "target-123"}

        with patch("quorum.gateway.boto3.client", return_value=client) as factory:
            result = provision_gateway(
                region_name="us-west-2",
                gateway_name="quorum-execution",
                role_arn="arn:aws:iam::123456789012:role/quorum-gateway",
                lambda_arn="arn:aws:lambda:us-west-2:123456789012:function:quorum-tools",
            )

        factory.assert_called_once_with("bedrock-agentcore-control", region_name="us-west-2")
        self.assertEqual(result["gateway_id"], "gw-123")
        self.assertEqual(client.create_gateway.call_args.kwargs["authorizerType"], "AWS_IAM")
        target = client.create_gateway_target.call_args.kwargs
        self.assertEqual(target["gatewayIdentifier"], "gw-123")
        self.assertEqual(
            target["credentialProviderConfigurations"],
            [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        self.assertEqual(
            len(target["targetConfiguration"]["mcp"]["lambda"]["toolSchema"]["inlinePayload"]),
            3,
        )


if __name__ == "__main__":
    unittest.main()
