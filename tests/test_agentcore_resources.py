from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from quorum.agentcore_resources import (
    create_gateway_for_verification,
    create_memory_for_verification,
    delete_gateway_and_wait,
    delete_memory_and_wait,
    discover_gateway_tools,
)


def _not_found(operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}},
        operation,
    )


class AgentCoreResourceLifecycleTest(unittest.TestCase):
    def test_memory_verification_creates_zero_events_and_checks_strategies(self) -> None:
        control = MagicMock()
        control.create_memory.return_value = {
            "memory": {"id": "QuorumMemory123", "status": "CREATING"}
        }
        control.get_memory.side_effect = [
            {"memory": {"id": "QuorumMemory123", "status": "CREATING"}},
            {
                "memory": {
                    "id": "QuorumMemory123",
                    "status": "ACTIVE",
                    "strategies": [
                        {"name": "QuorumSummaries"},
                        {"name": "QuorumFacts"},
                    ],
                }
            },
        ]

        evidence = create_memory_for_verification(
            control, name="QuorumMemory123", sleep=lambda _seconds: None
        )

        self.assertEqual(evidence.status, "ACTIVE")
        self.assertEqual(evidence.strategy_names, ("QuorumFacts", "QuorumSummaries"))
        self.assertEqual(evidence.event_count, 0)
        request = control.create_memory.call_args.kwargs
        self.assertEqual(request["eventExpiryDuration"], 7)
        self.assertEqual(request["tags"]["CostMode"], "ZeroModel")
        self.assertFalse(hasattr(control, "create_event") and control.create_event.called)

    def test_memory_failure_stops_without_waiting_for_timeout(self) -> None:
        control = MagicMock()
        control.create_memory.return_value = {
            "memory": {"id": "QuorumMemory123", "status": "CREATING"}
        }
        control.get_memory.return_value = {
            "memory": {
                "id": "QuorumMemory123",
                "status": "FAILED",
                "failureReason": "strategy unavailable",
            }
        }

        with self.assertRaisesRegex(RuntimeError, "strategy unavailable"):
            create_memory_for_verification(
                control, name="QuorumMemory123", sleep=lambda _seconds: None
            )

    def test_gateway_waits_for_ready_target_and_uses_iam(self) -> None:
        control = MagicMock()
        control.create_gateway.return_value = {
            "gatewayId": "QuorumGateway-123",
            "gatewayUrl": "https://example.gateway/mcp",
        }
        control.get_gateway.return_value = {"status": "READY"}
        control.create_gateway_target.return_value = {"targetId": "target-123"}
        control.get_gateway_target.return_value = {"status": "READY"}

        result = create_gateway_for_verification(
            control,
            name="QuorumGateway-123",
            role_arn="arn:aws:iam::123456789012:role/gateway",
            lambda_arn="arn:aws:lambda:ap-northeast-1:123456789012:function:tools",
            sleep=lambda _seconds: None,
        )

        self.assertEqual(result, ("QuorumGateway-123", "https://example.gateway/mcp", "target-123"))
        gateway_request = control.create_gateway.call_args.kwargs
        self.assertEqual(gateway_request["authorizerType"], "AWS_IAM")
        target_request = control.create_gateway_target.call_args.kwargs
        self.assertEqual(
            target_request["credentialProviderConfigurations"],
            [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        definitions = target_request["targetConfiguration"]["mcp"]["lambda"]["toolSchema"][
            "inlinePayload"
        ]
        self.assertEqual(len(definitions), 3)

    def test_gateway_terminal_failure_reports_service_reason(self) -> None:
        control = MagicMock()
        control.create_gateway.return_value = {
            "gatewayId": "QuorumGateway-123",
            "gatewayUrl": "https://example.gateway/mcp",
        }
        control.get_gateway.return_value = {"status": "READY"}
        control.create_gateway_target.return_value = {"targetId": "target-123"}
        control.get_gateway_target.return_value = {
            "status": "SYNCHRONIZE_UNSUCCESSFUL",
            "statusReasons": ["invalid schema"],
        }

        with self.assertRaisesRegex(RuntimeError, "invalid schema"):
            create_gateway_for_verification(
                control,
                name="QuorumGateway-123",
                role_arn="arn:aws:iam::123456789012:role/gateway",
                lambda_arn="arn:aws:lambda:ap-northeast-1:123456789012:function:tools",
                sleep=lambda _seconds: None,
            )

    def test_cleanup_waits_until_resources_are_missing(self) -> None:
        control = MagicMock()
        control.get_gateway_target.side_effect = [
            {"status": "READY"},
            {"status": "READY"},
            _not_found("GetGatewayTarget"),
        ]
        control.get_gateway.side_effect = [
            {"status": "READY"},
            {"status": "READY"},
            _not_found("GetGateway"),
        ]
        control.get_memory.side_effect = [
            {"memory": {"status": "ACTIVE"}},
            {"memory": {"status": "ACTIVE"}},
            _not_found("GetMemory"),
        ]

        delete_gateway_and_wait(
            control, "QuorumGateway-123", "target-123", sleep=lambda _seconds: None
        )
        delete_memory_and_wait(control, "QuorumMemory123", sleep=lambda _seconds: None)

        control.delete_gateway_target.assert_called_once()
        control.delete_gateway.assert_called_once()
        control.delete_memory.assert_called_once()

    def test_cleanup_is_idempotent_for_resources_already_deleting(self) -> None:
        control = MagicMock()
        control.get_gateway.side_effect = [
            {"status": "DELETING"},
            {"status": "DELETING"},
            _not_found("GetGateway"),
        ]
        control.get_memory.side_effect = [
            {"memory": {"status": "DELETING"}},
            {"memory": {"status": "DELETING"}},
            _not_found("GetMemory"),
        ]

        delete_gateway_and_wait(control, "QuorumGateway-123", None, sleep=lambda _: None)
        delete_memory_and_wait(control, "QuorumMemory123", sleep=lambda _: None)

        control.delete_gateway.assert_not_called()
        control.delete_memory.assert_not_called()

    def test_tool_discovery_performs_no_tool_call(self) -> None:
        tools = [
            MagicMock(tool_name=name)
            for name in (
                "calendar_create_tentative_event",
                "gmail_create_draft",
                "forms_create_response_request",
            )
        ]
        manager = MagicMock()
        manager.__enter__.return_value = tools
        manager.__exit__.return_value = None

        with patch("quorum.agentcore_resources.gateway_executor_tools", return_value=manager):
            names = discover_gateway_tools(
                endpoint="https://example.gateway/mcp", region_name="ap-northeast-1"
            )

        self.assertEqual(names, tuple(tool.tool_name for tool in tools))
        for tool in tools:
            tool.assert_not_called()

    def test_public_contract_documents_zero_event_and_zero_call_boundary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        api = (root / "API.md").read_text(encoding="utf-8")
        method = (root / "Method.md").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")

        self.assertIn("creates zero events", api)
        self.assertIn("runs zero `tools/call` requests", api)
        self.assertIn("QUORUM_EXECUTION_ENABLED=false", method)
        self.assertIn(
            "https://github.com/wellkilo/Quorum/actions/runs/33469765620",
            readme,
        )
        self.assertIn("created zero Memory events", readme)
        self.assertIn("zero Gateway `tools/call` requests", readme)
        self.assertIn("continuously hosted AgentCore backend are not claimed", readme)


if __name__ == "__main__":
    unittest.main()
