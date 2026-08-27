#!/usr/bin/env python3
"""Fail closed if the public Pages artifact overstates or loses its evidence boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_ARTIFACT_DIRECTORY = Path(__file__).parents[1] / "build" / "pages"


def validate_pages_demo(artifact_directory: Path) -> None:
    index = (artifact_directory / "index.html").read_text(encoding="utf-8")
    app = (artifact_directory / "assets" / "app.js").read_text(encoding="utf-8")
    favicon = (artifact_directory / "favicon.svg").read_text(encoding="utf-8")
    snapshot = json.loads((artifact_directory / "synthetic-week.json").read_text(encoding="utf-8"))

    required = {
        "index.html",
        "assets/style.css",
        "assets/app.js",
        "favicon.svg",
        "synthetic-week.json",
        ".nojekyll",
    }
    present = {
        str(path.relative_to(artifact_directory))
        for path in artifact_directory.rglob("*")
        if path.is_file()
    }
    missing = required - present
    if missing:
        raise ValueError(f"Pages artifact is missing: {', '.join(sorted(missing))}")
    if 'href="/assets/' in index or 'src="/assets/' in index or 'href="/"' in index:
        raise ValueError("Pages HTML must use project-relative URLs")
    if 'rel="icon" href="./favicon.svg" type="image/svg+xml"' not in index:
        raise ValueError("Pages HTML must link the project-relative SVG favicon")
    if "<svg" not in favicon or "<title" not in favicon:
        raise ValueError("Pages favicon must be an accessible SVG")
    if "wellkilo.github.io" not in app or "./synthetic-week.json" not in app:
        raise ValueError("Pages JavaScript must select the versioned static evidence fixture")
    if snapshot.get("data_classification") != "synthetic":
        raise ValueError("public replay must be classified as synthetic")
    if "not a measured real-world outcome" not in snapshot.get("disclaimer", ""):
        raise ValueError("public replay must reject a real-world impact interpretation")
    if snapshot.get("interrupt_budget_limit_per_person") != 2:
        raise ValueError("public replay must preserve the two-interrupt product boundary")
    if snapshot.get("baseline", {}).get("message_count") != 214:
        raise ValueError("public replay baseline changed without an evidence review")
    if snapshot.get("quorum", {}).get("interruption_count") != 6:
        raise ValueError("public replay outcome changed without an evidence review")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Quorum's GitHub Pages artifact.")
    parser.add_argument("--directory", type=Path, default=DEFAULT_ARTIFACT_DIRECTORY)
    args = parser.parse_args()
    validate_pages_demo(args.directory)
    print("Pages demo contract valid: synthetic provenance and relative assets confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
