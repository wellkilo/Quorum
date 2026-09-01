from __future__ import annotations

import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree

REPOSITORY_ROOT = Path(__file__).parents[1]
ARCHITECTURE_ASSET = REPOSITORY_ROOT / "assets" / "quorum-architecture.svg"
ARCHITECTURE_PNG = REPOSITORY_ROOT / "assets" / "quorum-architecture.png"
README = REPOSITORY_ROOT / "README.md"


class ArchitectureAssetTest(unittest.TestCase):
    def test_svg_is_accessible_and_uses_a_16_by_9_viewbox(self) -> None:
        root = ElementTree.parse(ARCHITECTURE_ASSET).getroot()

        self.assertEqual(root.attrib["viewBox"], "0 0 1600 900")
        self.assertEqual(root.attrib["role"], "img")
        self.assertEqual(root.attrib["aria-labelledby"], "title description")

    def test_svg_preserves_architecture_and_evidence_boundaries(self) -> None:
        content = ARCHITECTURE_ASSET.read_text(encoding="utf-8")

        for label in (
            "Listener",
            "Ledger Curator",
            "Risk Appraiser",
            "Quorum Router",
            "Executor",
            "SWARM · AMBIGUITY ONLY",
            "NATIVE HOOK",
            "POSTGRESQL",
            "STRANDS SESSION",
            "AGENTCORE MEMORY",
            "AGENTCORE RUNTIME",
            "PII-SAFE OPENTELEMETRY",
            "RUNTIME · MEMORY · GATEWAY · TRACE VERIFIED",
            "READY · tools/list · cleaned",
            "ACTIVE · 2 strategies · cleaned",
            "short-lived deploy verified · cleaned",
            "SHORT-LIVED AWS VERIFIED + CLEANED",
            "1 synthetic span · zero calls · cleaned",
            "MANAGED TRACE · SYNTHETIC PROBE · CLEANED",
            "GITHUB PAGES STATIC SYNTHETIC REPLAY",
        ):
            self.assertIn(label, content)

    def test_png_export_is_1600_by_900(self) -> None:
        content = ARCHITECTURE_PNG.read_bytes()

        self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", content[16:24]), (1600, 900))

    def test_readme_embeds_the_versioned_asset(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertIn(
            "![Quorum system architecture](assets/quorum-architecture.svg)",
            readme,
        )
        self.assertIn("[1600x900 PNG](assets/quorum-architecture.png)", readme)
        self.assertIn(
            "Runtime, Memory, and Gateway\n"
            "nodes record separate verified short-lived AWS lifecycles followed by cleanup",
            readme,
        )
        self.assertIn(
            "OpenTelemetry\nnode records one synthetic zero-call managed span followed by cleanup",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
