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
                    "favicon.svg",
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

    def test_validator_rejects_evidence_not_marked_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build_pages_demo(output)
            fixture_path = output / "synthetic-week.json"
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            fixture["data_classification"] = "real"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must be classified as synthetic"):
                validate_pages_demo(output)

    def test_pages_assets_are_project_relative_and_claim_static_evidence(self) -> None:
        index = (DEMO_DIRECTORY / "index.html").read_text(encoding="utf-8")
        app = (DEMO_DIRECTORY / "app.js").read_text(encoding="utf-8")

        self.assertIn('href="./assets/style.css"', index)
        self.assertIn('src="./assets/app.js"', index)
        self.assertIn('rel="icon" href="./favicon.svg" type="image/svg+xml"', index)
        self.assertNotIn('href="/"', index)
        self.assertIn("Static replay · synthetic data only", index)
        self.assertIn("No live AgentCore backend", index)
        self.assertIn('id="principles"', index)
        self.assertIn("Attention ledger", index)
        self.assertIn("Success is measured by necessary contact, not engagement.", index)
        self.assertIn("window.location.hostname === 'wellkilo.github.io'", app)
        self.assertIn("short-lived AgentCore Runtime deployment is verified separately", app)
        self.assertIn("this page is not AgentCore-hosted", app)

    def test_favicon_is_branded_accessible_svg(self) -> None:
        favicon = (DEMO_DIRECTORY / "favicon.svg").read_text(encoding="utf-8")

        self.assertIn('<title id="title">Quorum</title>', favicon)
        self.assertIn('aria-labelledby="title"', favicon)
        self.assertIn("#baf3da", favicon)

    def test_responsive_and_accessible_interaction_contract_is_preserved(self) -> None:
        index = (DEMO_DIRECTORY / "index.html").read_text(encoding="utf-8")
        style = (DEMO_DIRECTORY / "style.css").read_text(encoding="utf-8")
        app = (DEMO_DIRECTORY / "app.js").read_text(encoding="utf-8")

        self.assertIn('role="status" aria-live="polite"', index)
        self.assertIn('class="skip-link"', index)
        self.assertIn("@media (max-width: 760px)", style)
        self.assertIn("@media (max-width: 430px)", style)
        self.assertIn("@media (prefers-reduced-motion: reduce)", style)
        self.assertIn("button.setAttribute('aria-busy', 'true')", app)
        self.assertIn("button.removeAttribute('aria-busy')", app)


if __name__ == "__main__":
    unittest.main()
