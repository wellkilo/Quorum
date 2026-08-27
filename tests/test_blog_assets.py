from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
BLOG_DIRECTORY = REPOSITORY_ROOT / "docs" / "blog"
DEMO_URL = "https://wellkilo.github.io/Quorum/"
REPOSITORY_URL = "https://github.com/wellkilo/Quorum"


class BuilderBlogContractTest(unittest.TestCase):
    def test_exactly_three_numbered_drafts_have_required_title_phrase(self) -> None:
        drafts = sorted(BLOG_DIRECTORY.glob("[0-9][0-9]-*.md"))

        self.assertEqual(len(drafts), 3)
        for draft in drafts:
            title = draft.read_text(encoding="utf-8").splitlines()[0]
            self.assertTrue(title.startswith("# Agents for Humans:"), draft.name)

    def test_every_draft_links_public_evidence_and_bounds_claims(self) -> None:
        for draft in sorted(BLOG_DIRECTORY.glob("[0-9][0-9]-*.md")):
            content = draft.read_text(encoding="utf-8")

            self.assertIn(DEMO_URL, content, draft.name)
            self.assertIn(REPOSITORY_URL, content, draft.name)
            self.assertIn("synthetic", content.lower(), draft.name)
            self.assertIn("not", content.lower(), draft.name)

    def test_publication_manifest_keeps_urls_pending_until_manually_verified(self) -> None:
        content = (BLOG_DIRECTORY / "README.md").read_text(encoding="utf-8")

        self.assertEqual(content.count("Pending publication | No"), 3)
        self.assertIn("Do not mark", content)


if __name__ == "__main__":
    unittest.main()
