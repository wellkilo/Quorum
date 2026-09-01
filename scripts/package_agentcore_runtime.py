#!/usr/bin/env python3
"""Build Quorum's AgentCore Runtime CodeZip from a prepared dependency tree."""

from __future__ import annotations

import argparse
import shutil
import stat
import zipfile
from pathlib import Path

EXCLUDED_PARTS = {"__pycache__", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
REQUIRED_MEMBERS = {
    "bin/opentelemetry-instrument",
    "main.py",
    "quorum/runtime.py",
    "quorum/demo/index.html",
    "quorum/demo/favicon.svg",
    "bedrock_agentcore/__init__.py",
    "strands/__init__.py",
    "sqlalchemy/__init__.py",
}
MAX_COMPRESSED_BYTES = 250 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 750 * 1024 * 1024


def _included(path: Path) -> bool:
    return not EXCLUDED_PARTS.intersection(path.parts) and path.suffix not in EXCLUDED_SUFFIXES


def build_archive(
    staging_dir: Path, source_dir: Path, entrypoint: Path, output_path: Path
) -> tuple[int, int]:
    """Copy Quorum into staging and create a deterministic, validated ZIP."""

    package_destination = staging_dir / "quorum"
    if package_destination.exists():
        shutil.rmtree(package_destination)
    shutil.copytree(source_dir / "quorum", package_destination)
    shutil.copy2(entrypoint, staging_dir / "main.py")

    instrument = staging_dir / "bin/opentelemetry-instrument"
    if instrument.is_file():
        lines = instrument.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise RuntimeError("opentelemetry-instrument is empty")
        lines[0] = "#!/usr/bin/env python3"
        instrument.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for directory in (path for path in staging_dir.rglob("*") if path.is_dir()):
        directory.chmod(0o755)
    for file_path in (path for path in staging_dir.rglob("*") if path.is_file()):
        file_path.chmod(0o755 if file_path == instrument else 0o644)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(staging_dir.rglob("*")):
            if not path.is_file() or not _included(path.relative_to(staging_dir)):
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(staging_dir).as_posix())
            mode = 0o755 if path == instrument else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            with path.open("rb") as source:
                archive.writestr(info, source.read(), compress_type=zipfile.ZIP_DEFLATED)

    with zipfile.ZipFile(output_path) as archive:
        members = set(archive.namelist())
        uncompressed_size = sum(item.file_size for item in archive.infolist())
        bad_members = [
            name
            for name in members
            if "__pycache__" in Path(name).parts or Path(name).suffix in EXCLUDED_SUFFIXES
        ]
    missing = sorted(REQUIRED_MEMBERS - members)
    if missing:
        raise RuntimeError(f"CodeZip is missing required members: {', '.join(missing)}")
    if bad_members:
        raise RuntimeError(f"CodeZip contains excluded bytecode: {bad_members[0]}")
    with zipfile.ZipFile(output_path) as archive:
        instrument_text = archive.read("bin/opentelemetry-instrument").decode("utf-8")
        instrument_mode = archive.getinfo("bin/opentelemetry-instrument").external_attr >> 16
    if not instrument_text.startswith("#!/usr/bin/env python3\n"):
        raise RuntimeError("CodeZip has a non-portable OpenTelemetry entrypoint shebang")
    if instrument_mode & 0o111 == 0:
        raise RuntimeError("CodeZip OpenTelemetry entrypoint is not executable")
    size = output_path.stat().st_size
    if size > MAX_COMPRESSED_BYTES:
        raise RuntimeError(f"CodeZip is too large: {size} bytes")
    if uncompressed_size > MAX_UNCOMPRESSED_BYTES:
        raise RuntimeError(f"CodeZip expands beyond the limit: {uncompressed_size} bytes")
    return size, len(members)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, default=Path("src"))
    parser.add_argument("--entrypoint", type=Path, default=Path("agentcore_main.py"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    size, members = build_archive(args.staging_dir, args.source_dir, args.entrypoint, args.output)
    print(f"artifact={args.output.resolve()}")
    print(f"compressed_bytes={size}")
    print(f"files={members}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
