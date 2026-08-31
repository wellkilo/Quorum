from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_agentcore_runtime import REQUIRED_MEMBERS, build_archive


class AgentCorePackageTest(unittest.TestCase):
    def test_archive_contains_runtime_dependencies_and_excludes_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staging = root / "staging"
            source = root / "src"
            entrypoint = root / "agentcore_main.py"
            output = root / "runtime.zip"
            staging.mkdir()
            for member in REQUIRED_MEMBERS - {
                "main.py",
                "quorum/runtime.py",
                "quorum/demo/index.html",
                "quorum/demo/favicon.svg",
            }:
                path = staging / member
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            package = source / "quorum"
            (package / "demo").mkdir(parents=True)
            (package / "runtime.py").write_text("app = object()\n", encoding="utf-8")
            (package / "demo/index.html").write_text("<main>Quorum</main>", encoding="utf-8")
            (package / "demo/favicon.svg").write_text("<svg/>", encoding="utf-8")
            (package / "__pycache__").mkdir()
            (package / "__pycache__/runtime.pyc").write_bytes(b"not-portable")
            entrypoint.write_text("from quorum.runtime import app\napp.run()\n", encoding="utf-8")

            size, member_count = build_archive(staging, source, entrypoint, output)

            self.assertGreater(size, 0)
            self.assertEqual(member_count, len(REQUIRED_MEMBERS))
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                packaged_entrypoint = archive.read("main.py").decode("utf-8")
            self.assertEqual(names, REQUIRED_MEMBERS)
            self.assertIn("from quorum.runtime import app", packaged_entrypoint)

    def test_archive_rejects_missing_runtime_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staging = root / "staging"
            source = root / "src"
            entrypoint = root / "agentcore_main.py"
            staging.mkdir()
            (source / "quorum/demo").mkdir(parents=True)
            (source / "quorum/runtime.py").write_text("app = object()\n", encoding="utf-8")
            (source / "quorum/demo/index.html").write_text("", encoding="utf-8")
            (source / "quorum/demo/favicon.svg").write_text("", encoding="utf-8")
            entrypoint.write_text("from quorum.runtime import app\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "missing required members"):
                build_archive(staging, source, entrypoint, root / "runtime.zip")


if __name__ == "__main__":
    unittest.main()
