from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.manage_agentcore_observability import PRIVACY_SENTINEL, prepare, restore, verify


class AgentCoreObservabilityTest(unittest.TestCase):
    def test_prepare_records_state_and_enables_zero_index_transaction_search(self) -> None:
        logs = MagicMock()
        logs.describe_resource_policies.return_value = {"resourcePolicies": []}
        logs.describe_log_groups.return_value = {"logGroups": []}
        xray = MagicMock()
        xray.get_trace_segment_destination.side_effect = [
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
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            args = argparse.Namespace(
                account_id="123456789012",
                region="ap-northeast-1",
                policy_name="QuorumTransactionSearchVerification-123-1",
                state_file=state_file,
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray)
                prepare(args)

            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(state["previous_destination"], "XRay")
        self.assertTrue(state["policy_created"])
        self.assertFalse(state["shared_span_log_group_preexisting"])
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
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "policy_name": "QuorumTransactionSearchVerification-123-1",
                        "policy_created": True,
                        "previous_destination": "XRay",
                        "previous_indexing_percentage": 0.0,
                        "shared_span_log_group_preexisting": False,
                        "region": "ap-northeast-1",
                    }
                ),
                encoding="utf-8",
            )
            with patch("scripts.manage_agentcore_observability.boto3.Session") as session_factory:
                session_factory.return_value.client.side_effect = (logs, xray)
                restore(argparse.Namespace(state_file=state_file))

        logs.delete_resource_policy.assert_called_once_with(
            policyName="QuorumTransactionSearchVerification-123-1"
        )
        xray.update_trace_segment_destination.assert_not_called()
        xray.update_indexing_rule.assert_not_called()
        logs.delete_log_group.assert_called_once_with(logGroupName="aws/spans")

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
