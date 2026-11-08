#!/usr/bin/env python3
"""Rebuild architecture/index.html from architecture/sections/*.md.

The atlas ships as a single self-contained HTML file, but its chapters are authored as Markdown
under architecture/sections/. index.html embeds each chapter verbatim in a `const SECTIONS = [...]`
literal, so the two copies have to move together.

Nothing made them move together, and they drifted: 25_block_contract.md gained about 1100
characters that never reached index.html, so the rendered Block Wire Contract chapter (the one
anyone actually opens) was a truncated copy of its own source for an unknown number of commits.
Editing Markdown and forgetting to re-render is not a mistake anyone should have to remember not
to make, hence this script and its --check mode.

Usage:
    python scripts/build_atlas.py            # regenerate index.html from the sections
    python scripts/build_atlas.py --check    # exit 1 if index.html is stale (for CI)

Chapter order, titles and groups live in architecture/atlas.json, because none of the three can be
derived from the Markdown: the display order is by group rather than by file number, and the titles
are curated ("Data Model (SQLite)" rather than the chapter's own H1).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATLAS = ROOT / "architecture"
INDEX = ATLAS / "index.html"
MANIFEST = ATLAS / "atlas.json"
SECTIONS_DIR = ATLAS / "sections"

SECTIONS_LINE = re.compile(r"^const SECTIONS = \[.*\];$", re.M)


def chapter_id(filename: str) -> str:
    """sections/00_overview.md -> sec-00-overview-md, matching the anchors already in index.html."""
    return "sec-" + filename.replace("_", "-").replace(".", "-")


def build_sections() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST.read_text())
    sections = []
    for entry in manifest:
        path = SECTIONS_DIR / entry["file"]
        if not path.exists():
            raise SystemExit(
                f"atlas.json lists {entry['file']}, which does not exist under "
                f"architecture/sections/. Remove the entry or restore the chapter."
            )
        sections.append(
            {
                "id": chapter_id(entry["file"]),
                "title": entry["title"],
                "group": entry["group"],
                "md": path.read_text(),
            }
        )

    listed = {e["file"] for e in manifest}
    orphans = sorted(p.name for p in SECTIONS_DIR.glob("*.md") if p.name not in listed)
    if orphans:
        raise SystemExit(
            f"chapter(s) {orphans} exist under architecture/sections/ but are not in atlas.json, "
            f"so they would render nowhere. Add them (with a group and title) or delete them."
        )
    return sections


def render(sections: list[dict[str, str]]) -> str:
    html = INDEX.read_text()
    if not SECTIONS_LINE.search(html):
        raise SystemExit(
            "could not find the `const SECTIONS = [...];` line in architecture/index.html. "
            "The shell was restructured; update SECTIONS_LINE in this script to match."
        )
    payload = json.dumps(sections, ensure_ascii=False)
    return SECTIONS_LINE.sub(lambda _: f"const SECTIONS = {payload};", html, count=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if index.html does not match the sections",
    )
    opts = ap.parse_args()

    rendered = render(build_sections())
    current = INDEX.read_text()

    if rendered == current:
        print("architecture/index.html is up to date with architecture/sections/.")
        return 0

    if opts.check:
        print(
            "architecture/index.html is STALE: it does not match architecture/sections/.\n"
            "A chapter was edited without re-rendering the atlas, so the browsable copy and the\n"
            "Markdown source disagree. Fix with:\n"
            "    python scripts/build_atlas.py",
            file=sys.stderr,
        )
        return 1

    INDEX.write_text(rendered)
    print(f"rebuilt architecture/index.html from {len(build_sections())} chapters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
