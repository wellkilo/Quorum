from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from quorum.observability import (
    configure_strands_trace_redaction,
    safe_trace_attributes,
    traced_operation,
)


class ObservabilityTest(unittest.TestCase):
    def test_redaction_opt_in_is_added_once_without_dropping_existing_options(self) -> None:
        with patch.dict(os.environ, {"OTEL_SEMCONV_STABILITY_OPT_IN": "http/dup"}, clear=True):
            configure_strands_trace_redaction()
            configure_strands_trace_redaction()

            options = os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"].split(",")

        self.assertIn("http/dup", options)
        self.assertEqual(options.count("gen_ai_unredacted_attributes="), 1)

    def test_safe_attributes_reject_content_and_non_scalar_values(self) -> None:
        self.assertEqual(
            safe_trace_attributes(
                {
                    "quorum.organization_id": "org_opaque",
                    "quorum.interrupt_count": 2,
                }
            ),
            {"quorum.organization_id": "org_opaque", "quorum.interrupt_count": 2},
        )
        with self.assertRaisesRegex(ValueError, "not allow-listed"):
            safe_trace_attributes({"quorum.prompt": "private text"})
        with self.assertRaisesRegex(TypeError, "must be scalar"):
            safe_trace_attributes({"quorum.organization_id": ["org_opaque"]})

    def test_traced_operation_sets_only_validated_attributes(self) -> None:
        tracer = MagicMock()
        span = MagicMock()
        tracer.start_as_current_span.return_value.__enter__.return_value = span

        with (
            patch("quorum.observability.trace.get_tracer", return_value=tracer),
            traced_operation("quorum.test", {"quorum.data_classification": "synthetic"}),
        ):
            pass

        tracer.start_as_current_span.assert_called_once_with("quorum.test")
        span.set_attribute.assert_called_once_with("quorum.data_classification", "synthetic")


if __name__ == "__main__":
    unittest.main()
