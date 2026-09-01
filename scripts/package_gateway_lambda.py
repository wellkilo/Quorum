#!/usr/bin/env python3
"""Build a deterministic Lambda CodeZip for Quorum's real Gateway target."""

from __future__ import annotations

import argparse
import shutil
import stat
import zipfile
from pathlib import Path

EXCLUDED_PARTS = {"__pycache__", ".DS_Store", "tests"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
REQUIRED_MEMBERS = {
    "quorum/gateway.py",
    "quorum/database.py",
    "quorum/execution.py",
    "quorum/providers.py",
    "pydantic/__init__.py",
    "sqlalchemy/__init__.py",
    "googleapiclient/__init__.py",
    "slack_sdk/__init__.py",
}
MAX_COMPRESSED_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024


def _included(path: Path) -> bool:
    return not EXCLUDED_PARTS.intersection(path.parts) and path.suffix not in EXCLUDED_SUFFIXES


def build_archive(staging_dir: Path, source_dir: Path, output_path: Path) -> tuple[int, int]:
    """Copy the Quorum package into prepared dependencies and validate Lambda limits."""

    package_destination = staging_dir / "quorum"
    if package_destination.exists():
        shutil.rmtree(package_destination)
    shutil.copytree(source_dir / "quorum", package_destination)
    for directory in (path for path in staging_dir.rglob("*") if path.is_dir()):
        directory.chmod(0o755)
    for file_path in (path for path in staging_dir.rglob("*") if path.is_file()):
        file_path.chmod(0o644)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(staging_dir.rglob("*")):
            relative = path.relative_to(staging_dir)
            if not path.is_file() or not _included(relative):
                continue
            info = zipfile.ZipInfo.from_file(path, relative.as_posix())
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with path.open("rb") as source:
                archive.writestr(info, source.read(), compress_type=zipfile.ZIP_DEFLATED)

    with zipfile.ZipFile(output_path) as archive:
        members = set(archive.namelist())
        uncompressed_size = sum(item.file_size for item in archive.infolist())
        bad_members = [
            name
            for name in members
            if EXCLUDED_PARTS.intersection(Path(name).parts)
            or Path(name).suffix in EXCLUDED_SUFFIXES
        ]
    missing = sorted(REQUIRED_MEMBERS - members)
    if missing:
        raise RuntimeError(f"Lambda CodeZip is missing required members: {', '.join(missing)}")
    if bad_members:
        raise RuntimeError(f"Lambda CodeZip contains excluded content: {bad_members[0]}")
    compressed_size = output_path.stat().st_size
    if compressed_size > MAX_COMPRESSED_BYTES:
        raise RuntimeError(f"Lambda CodeZip is too large: {compressed_size} bytes")
    if uncompressed_size > MAX_UNCOMPRESSED_BYTES:
        raise RuntimeError(f"Lambda CodeZip expands beyond the limit: {uncompressed_size} bytes")
    return compressed_size, len(members)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, default=Path("src"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    size, members = build_archive(args.staging_dir, args.source_dir, args.output)
    print(f"artifact={args.output.resolve()}")
    print(f"compressed_bytes={size}")
    print(f"files={members}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
