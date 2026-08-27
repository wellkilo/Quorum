#!/usr/bin/env python3
"""Build the GitHub Pages artifact from Quorum's shared demo sources."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
SOURCE_DIRECTORY = REPOSITORY_ROOT / "src" / "quorum" / "demo"
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "build" / "pages"
PAGES_FILES = {
    "index.html": Path("index.html"),
    "style.css": Path("assets/style.css"),
    "app.js": Path("assets/app.js"),
    "synthetic-week.json": Path("synthetic-week.json"),
    ".nojekyll": Path(".nojekyll"),
}


def build_pages_demo(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    for source_name, destination_name in PAGES_FILES.items():
        source = SOURCE_DIRECTORY / source_name
        destination = output_directory / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Quorum's static GitHub Pages demo.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    args = parser.parse_args()
    build_pages_demo(args.output)
    print(f"Pages demo built at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
