from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from scripts.manage_agentcore_observability import (
    PRIVACY_SENTINEL,
    _application_signals_channel_arns,
    prepare,
    restore,
    verify,
)


class AgentCoreObservabilityTest(unittest.TestCase):
    def test_application_signals_channel_scan_is_paginated_and_scoped(self) -> None:
        cloudtrail = MagicMock()
        application_signals_channel = (
            "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
            "channel/aws-service-channel/application-signals/default"
        )
        cloudtrail.list_channels.side_effect = [
            {
                "Channels": [
                    {
                        "ChannelArn": (
                            "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
                            "channel/aws-service-channel/resource-explorer-2"
                        )
                    }
                ],
                "NextToken": "page-2",
            },
            {"Channels": [{"ChannelArn": application_signals_channel}]},
        ]

        self.assertEqual(
            _application_signals_channel_arns(cloudtrail), {application_signals_channel}
        )
        self.assertEqual(
            cloudtrail.list_channels.call_args_list,
            [unittest.mock.call(), unittest.mock.call(NextToken="page-2")],
        )

    def test_prepare_records_state_and_enables_zero_index_transaction_search(self) -> None:
        logs = MagicMock()
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        logs.describe_log_groups.return_value = {"logGroups": []}
        xray = MagicMock()
        xray.get_trace_segment_destination.side_effect = [
            {"Destination": "XRay", "Status": "ACTIVE"},
            {"Destination": "XRay", "Status": "ACTIVE"},
            {"Destination": "CloudWatchLogs", "Status": "ACTIVE"},
        ]
        xray.get_indexing_rules.return_value = {
            "IndexingRules": [
                {
                    "Name": "Default",
                    "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
                }
            ]
        }
        iam = MagicMock()
        iam.get_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity", "Message": "not found"}},
            "GetRole",
        )
        cloudtrail = MagicMock()
        cloudtrail.list_channels.return_value = {
            "Channels": [
                {
                    "ChannelArn": (
                        "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
                        "channel/aws-service-channel/resource-explorer-2"
                    )
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            args = argparse.Namespace(
                account_id="123456789012",
                region="ap-northeast-1",
                policy_name="QuorumTransactionSearchVerification-123-1",
                state_file=state_file,
                transition_timeout_seconds=1,
                poll_seconds=0,
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray, iam, cloudtrail)
                prepare(args)

            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(state["previous_destination"], "XRay")
        self.assertTrue(state["policy_created"])
        self.assertEqual(
            state["managed_log_groups_preexisting"],
            {"aws/spans": False, "/aws/application-signals/data": False},
        )
        self.assertFalse(state["application_signals_role_preexisting"])
        self.assertTrue(state["application_signals_role_created"])
        self.assertEqual(state["application_signals_channel_arns_preexisting"], [])
        iam.create_service_linked_role.assert_called_once_with(
            AWSServiceName="application-signals.cloudwatch.amazonaws.com"
        )
        logs.put_resource_policy.assert_called_once()
        xray.update_trace_segment_destination.assert_called_once_with(Destination="CloudWatchLogs")
        xray.update_indexing_rule.assert_called_once_with(
            Name="Default",
            Rule={"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
        )

    def test_restore_reinstates_prior_settings_and_deletes_only_named_policy(self) -> None:
        logs = MagicMock()
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        logs.describe_log_groups.side_effect = [
            {"logGroups": [{"logGroupName": "aws/spans"}]},
            {"logGroups": []},
            {"logGroups": [{"logGroupName": "/aws/application-signals/data"}]},
            {"logGroups": []},
        ]
        xray = MagicMock()
        xray.get_trace_segment_destination.return_value = {
            "Destination": "XRay",
            "Status": "ACTIVE",
        }
        xray.get_indexing_rules.return_value = {
            "IndexingRules": [
                {
                    "Name": "Default",
                    "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
                }
            ]
        }
        iam = MagicMock()
        iam.get_role.side_effect = [
            {"Role": {"RoleName": "AWSServiceRoleForCloudWatchApplicationSignals"}},
            ClientError(
                {"Error": {"Code": "NoSuchEntity", "Message": "not found"}},
                "GetRole",
            ),
        ]
        iam.delete_service_linked_role.return_value = {"DeletionTaskId": "task-123"}
        iam.get_service_linked_role_deletion_status.return_value = {"Status": "SUCCEEDED"}
        resource_explorer_channel = (
            "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
            "channel/aws-service-channel/resource-explorer-2"
        )
        application_signals_channel = (
            "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
            "channel/aws-service-channel/application-signals/default"
        )
        cloudtrail = MagicMock()
        cloudtrail.list_channels.return_value = {
            "Channels": [
                {"ChannelArn": resource_explorer_channel},
                {"ChannelArn": application_signals_channel},
            ]
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "policy_name": "QuorumTransactionSearchVerification-123-1",
                        "policy_created": True,
                        "previous_destination": "XRay",
                        "previous_indexing_percentage": 0.0,
                        "managed_log_groups_preexisting": {
                            "aws/spans": False,
                            "/aws/application-signals/data": False,
                        },
                        "application_signals_role_preexisting": False,
                        "application_signals_role_created": True,
                        "application_signals_channel_arns_preexisting": [],
                        "region": "ap-northeast-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray, iam, cloudtrail)
                restore(
                    argparse.Namespace(
                        state_file=state_file,
                        transition_timeout_seconds=1,
                        poll_seconds=0,
                    )
                )

        logs.delete_resource_policy.assert_called_once_with(
            policyName="QuorumTransactionSearchVerification-123-1"
        )
        xray.update_trace_segment_destination.assert_not_called()
        xray.update_indexing_rule.assert_not_called()
        self.assertEqual(
            logs.delete_log_group.call_args_list,
            [
                unittest.mock.call(logGroupName="aws/spans"),
                unittest.mock.call(logGroupName="/aws/application-signals/data"),
            ],
        )
        cloudtrail.delete_channel.assert_not_called()
        iam.delete_service_linked_role.assert_called_once_with(
            RoleName="AWSServiceRoleForCloudWatchApplicationSignals"
        )
        iam.get_service_linked_role_deletion_status.assert_called_once_with(
            DeletionTaskId="task-123"
        )

    def test_restore_preserves_preexisting_application_signals_resources(self) -> None:
        logs = MagicMock()
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        xray = MagicMock()
        xray.get_trace_segment_destination.return_value = {
            "Destination": "XRay",
            "Status": "ACTIVE",
        }
        xray.get_indexing_rules.return_value = {
            "IndexingRules": [
                {
                    "Name": "Default",
                    "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
                }
            ]
        }
        iam = MagicMock()
        channel = (
            "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
            "channel/aws-service-channel/application-signals/preexisting"
        )
        cloudtrail = MagicMock()
        cloudtrail.list_channels.return_value = {"Channels": [{"ChannelArn": channel}]}
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "policy_name": "QuorumTransactionSearchVerification-123-1",
                        "policy_created": False,
                        "previous_destination": "XRay",
                        "previous_indexing_percentage": 0.0,
                        "managed_log_groups_preexisting": {
                            "aws/spans": True,
                            "/aws/application-signals/data": True,
                        },
                        "application_signals_role_preexisting": True,
                        "application_signals_role_created": False,
                        "application_signals_channel_arns_preexisting": [channel],
                        "region": "ap-northeast-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray, iam, cloudtrail)
                restore(
                    argparse.Namespace(
                        state_file=state_file,
                        transition_timeout_seconds=1,
                        poll_seconds=0,
                    )
                )

        logs.delete_log_group.assert_not_called()
        cloudtrail.delete_channel.assert_not_called()
        iam.get_role.assert_not_called()
        iam.delete_service_linked_role.assert_not_called()

    def test_restore_waits_for_pending_destination_before_switching_back(self) -> None:
        logs = MagicMock()
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        xray = MagicMock()
        xray.get_trace_segment_destination.side_effect = [
            {"Destination": "CloudWatchLogs", "Status": "PENDING"},
            {"Destination": "CloudWatchLogs", "Status": "ACTIVE"},
            {"Destination": "XRay", "Status": "ACTIVE"},
        ]
        xray.get_indexing_rules.return_value = {
            "IndexingRules": [
                {
                    "Name": "Default",
                    "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
                }
            ]
        }
        iam = MagicMock()
        cloudtrail = MagicMock()
        cloudtrail.list_channels.return_value = {"Channels": []}
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "policy_name": "QuorumTransactionSearchVerification-123-1",
                        "policy_created": False,
                        "previous_destination": "XRay",
                        "previous_indexing_percentage": 0.0,
                        "managed_log_groups_preexisting": {
                            "aws/spans": True,
                            "/aws/application-signals/data": True,
                        },
                        "application_signals_role_preexisting": True,
                        "application_signals_role_created": False,
                        "application_signals_channel_arns_preexisting": [],
                        "region": "ap-northeast-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray, iam, cloudtrail)
                restore(
                    argparse.Namespace(
                        state_file=state_file,
                        transition_timeout_seconds=1,
                        poll_seconds=0,
                    )
                )

        xray.update_trace_segment_destination.assert_called_once_with(Destination="XRay")

    def test_restore_treats_already_deleted_policy_and_role_as_success(self) -> None:
        logs = MagicMock()
        logs.delete_resource_policy.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}},
            "DeleteResourcePolicy",
        )
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        logs.describe_log_groups.return_value = {"logGroups": []}
        xray = MagicMock()
        xray.get_trace_segment_destination.return_value = {
            "Destination": "XRay",
            "Status": "ACTIVE",
        }
        xray.get_indexing_rules.return_value = {
            "IndexingRules": [
                {
                    "Name": "Default",
                    "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
                }
            ]
        }
        iam = MagicMock()
        iam.get_role.side_effect = [
            {"Role": {"RoleName": "AWSServiceRoleForCloudWatchApplicationSignals"}},
            ClientError(
                {"Error": {"Code": "NoSuchEntity", "Message": "gone"}},
                "GetRole",
            ),
        ]
        iam.delete_service_linked_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntityException", "Message": "gone"}},
            "DeleteServiceLinkedRole",
        )
        cloudtrail = MagicMock()
        cloudtrail.list_channels.return_value = {"Channels": []}
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "policy_name": "QuorumTransactionSearchVerification-123-1",
                        "policy_created": True,
                        "previous_destination": "XRay",
                        "previous_indexing_percentage": 0.0,
                        "managed_log_groups_preexisting": {
                            "aws/spans": False,
                            "/aws/application-signals/data": False,
                        },
                        "application_signals_role_preexisting": False,
                        "application_signals_role_created": True,
                        "application_signals_channel_arns_preexisting": [],
                        "region": "ap-northeast-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray, iam, cloudtrail)
                restore(
                    argparse.Namespace(
                        state_file=state_file,
                        transition_timeout_seconds=1,
                        poll_seconds=0,
                    )
                )

        iam.get_service_linked_role_deletion_status.assert_not_called()

    def test_restore_continues_independent_cleanup_after_log_group_failure(self) -> None:
        logs = MagicMock()
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        logs.describe_log_groups.side_effect = RuntimeError("logs unavailable")
        xray = MagicMock()
        xray.get_trace_segment_destination.return_value = {
            "Destination": "XRay",
            "Status": "ACTIVE",
        }
        xray.get_indexing_rules.return_value = {
            "IndexingRules": [
                {
                    "Name": "Default",
                    "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 0.0}},
                }
            ]
        }
        iam = MagicMock()
        iam.get_role.side_effect = [
            {"Role": {"RoleName": "AWSServiceRoleForCloudWatchApplicationSignals"}},
            ClientError(
                {"Error": {"Code": "NoSuchEntity", "Message": "not found"}},
                "GetRole",
            ),
        ]
        iam.delete_service_linked_role.return_value = {"DeletionTaskId": "task-123"}
        iam.get_service_linked_role_deletion_status.return_value = {"Status": "SUCCEEDED"}
        channel = (
            "arn:aws:cloudtrail:ap-northeast-1:123456789012:"
            "channel/aws-service-channel/application-signals/default"
        )
        cloudtrail = MagicMock()
        cloudtrail.list_channels.return_value = {"Channels": [{"ChannelArn": channel}]}
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "policy_name": "QuorumTransactionSearchVerification-123-1",
                        "policy_created": False,
                        "previous_destination": "XRay",
                        "previous_indexing_percentage": 0.0,
                        "managed_log_groups_preexisting": {
                            "aws/spans": False,
                            "/aws/application-signals/data": False,
                        },
                        "application_signals_role_preexisting": False,
                        "application_signals_role_created": True,
                        "application_signals_channel_arns_preexisting": [],
                        "region": "ap-northeast-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray, iam, cloudtrail)
                with self.assertRaisesRegex(RuntimeError, "managed log group cleanup failed"):
                    restore(
                        argparse.Namespace(
                            state_file=state_file,
                            transition_timeout_seconds=1,
                            poll_seconds=0,
                        )
                    )

        cloudtrail.delete_channel.assert_not_called()
        iam.delete_service_linked_role.assert_called_once_with(
            RoleName="AWSServiceRoleForCloudWatchApplicationSignals"
        )

    def test_verify_matches_managed_ids_and_rejects_forbidden_content(self) -> None:
        logs = MagicMock()
        logs.describe_log_streams.return_value = {"logStreams": [{"logStreamName": "spans"}]}
        span = {
            "traceId": "1" * 32,
            "spanId": "2" * 16,
            "name": "quorum.observability.probe",
            "attributes": {
                "quorum.probe_id": "probe_123",
                "quorum.organization_id": "org_synthetic",
                "quorum.session_id": "session_00000000000000000000000001",
                "quorum.data_classification": "synthetic",
            },
        }
        logs.get_log_events.return_value = {"events": [{"message": json.dumps(span)}]}
        args = argparse.Namespace(
            region="ap-northeast-1",
            runtime_id="QuorumRuntime-abc",
            probe_id="probe_123",
            session_id="session_00000000000000000000000001",
            trace_id="1" * 32,
            span_id="2" * 16,
            start_time_ms=1,
            timeout_seconds=1,
            poll_seconds=0,
        )
        with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
            session_factory.return_value.client.return_value = logs
            verify(args)

            leaked = dict(span)
            leaked["attributes"] = dict(span["attributes"], payload=PRIVACY_SENTINEL)
            logs.get_log_events.return_value = {"events": [{"message": json.dumps(leaked)}]}
            with self.assertRaisesRegex(RuntimeError, "forbidden markers"):
                verify(args)


if __name__ == "__main__":
    unittest.main()
