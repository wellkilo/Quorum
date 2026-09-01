from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_gateway_lambda import REQUIRED_MEMBERS, build_archive


class GatewayLambdaPackageTest(unittest.TestCase):
    def test_archive_contains_handler_dependencies_and_excludes_tests_and_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            staging = root / "staging"
            source = root / "src"
            output = root / "gateway.zip"
            staging.mkdir()
            for member in REQUIRED_MEMBERS - {
                "quorum/gateway.py",
                "quorum/database.py",
                "quorum/execution.py",
                "quorum/providers.py",
            }:
                path = staging / member
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            package = source / "quorum"
            package.mkdir(parents=True)
            for name in ("gateway.py", "database.py", "execution.py", "providers.py"):
                (package / name).write_text("# fixture\n", encoding="utf-8")
            (package / "__pycache__").mkdir()
            (package / "__pycache__/gateway.pyc").write_bytes(b"bytecode")
            (staging / "tests").mkdir()
            (staging / "tests/test_dependency.py").write_text("", encoding="utf-8")

            size, member_count = build_archive(staging, source, output)

            self.assertGreater(size, 0)
            self.assertEqual(member_count, len(REQUIRED_MEMBERS))
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), REQUIRED_MEMBERS)


if __name__ == "__main__":
    unittest.main()
