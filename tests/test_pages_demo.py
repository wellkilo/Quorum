from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quorum.replay import ReplayStore
from scripts.build_pages_demo import build_pages_demo
from scripts.validate_pages_demo import validate_pages_demo

DEMO_DIRECTORY = Path(__file__).parents[1] / "src" / "quorum" / "demo"


class PagesDemoContractTest(unittest.TestCase):
    def test_builder_produces_the_exact_pages_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            build_pages_demo(output)
            validate_pages_demo(output)

            self.assertEqual(
                {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()},
                {
                    "index.html",
                    "assets/style.css",
                    "assets/app.js",
                    "synthetic-week.json",
                    ".nojekyll",
                },
            )

    def test_static_fixture_matches_runtime_evidence_contract(self) -> None:
        fixture = json.loads((DEMO_DIRECTORY / "synthetic-week.json").read_text(encoding="utf-8"))
        runtime = ReplayStore().start().model_dump(mode="json")

        fixture.pop("replay_id")
        runtime.pop("replay_id")

        self.assertEqual(fixture, runtime)

    def test_pages_assets_are_project_relative_and_claim_static_evidence(self) -> None:
        index = (DEMO_DIRECTORY / "index.html").read_text(encoding="utf-8")
        app = (DEMO_DIRECTORY / "app.js").read_text(encoding="utf-8")

        self.assertIn('href="./assets/style.css"', index)
        self.assertIn('src="./assets/app.js"', index)
        self.assertNotIn('href="/"', index)
        self.assertIn("Public static replay · synthetic data only", index)
        self.assertIn("No live AgentCore backend", index)
        self.assertIn("window.location.hostname === 'wellkilo.github.io'", app)
        self.assertIn("AgentCore deployment is not claimed", app)


if __name__ == "__main__":
    unittest.main()
