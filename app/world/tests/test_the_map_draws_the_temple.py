"""The temple exists in the world and was missing from the map.

Reported from a run: *"Kasukabe Temple doesn't even exist in the map."*
Both halves were true at once, which is what made it confusing:

  * the WORLD has carried it the whole time - `build_kasukabe_genesis_map`
    mints sector **900001, "Kasukabe Temple"**, `known_places` lists it,
    `move` walks there, and `pray`/`marry` are gated on it;
  * the VIEWER did not. `village.js` had thirty zones and no temple,
    `village2.js` registered none, and `township-v2.js` - 272 KB, the map
    a person actually looks at - contained the string "temple" **zero**
    times, while `agents.js` mentioned it 105 times in the residents' own
    personas.

So a resident walked to the temple in the world and was drawn standing
nowhere, while its persona talked about the place. DESIGN section 1 lists
the temple as a genesis building, section 4 puts marriage in it, and
section 7 gives it weddings, prayers and donations.

Twelve reviewers reading 140,000 lines did not find this. One run did.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TOWNSHIP = Path(__file__).resolve().parents[1].parent / "game" / "static" / "js" / "township"


def _read(name: str) -> str:
    return (TOWNSHIP / name).read_text(encoding="utf-8")


def test_the_world_really_does_have_a_temple_sector():
    """The half that was always true - pinned, so the map is what moves."""
    from world.map.expansion import TEMPLE_SECTOR_NAME

    assert TEMPLE_SECTOR_NAME == "Kasukabe Temple"


def test_the_flat_map_places_the_temple():
    src = _read("village.js")
    assert re.search(r"^\s*temple:\s*\{", src, re.M), (
        "village.js has no temple zone, so a resident who walks to the "
        "temple in the world is drawn standing nowhere"
    )


def test_the_temple_is_not_drawn_as_a_cottage():
    """`ROOM_STYLE[plan.style] || ROOM_STYLE.house` - an unknown style
    silently falls back to a house."""
    src = _read("village.js")
    assert re.search(r"^\s*temple:\s*\['#", src, re.M), (
        "the temple has no palette, so it falls through to ROOM_STYLE.house "
        "and the town's only religious building is drawn as a cottage"
    )


def test_the_v2_map_registers_it_too():
    src = _read("village2.js")
    assert "zone('temple'" in src, "village2.js draws no temple"


def test_the_viewer_treats_it_as_a_civic_place():
    """DESIGN section 7 gives it weddings, prayers and donations, and
    `pray`/`marry` are gated on it - it is civic, not a private house."""
    for name in ("township-v2.js", "township.js"):
        src = _read(name)
        i = src.index("const CIVIC_ZONES")
        block = src[i : src.index("];", i)]
        assert "'temple'" in block, f"{name} does not count the temple as civic"


def test_the_temple_does_not_overlap_another_zone():
    """The ground it was given was verified empty; this keeps it that way."""
    src = _read("village.js")
    i = src.index("const ZONES = {")
    body = src[i : src.index("\n};", i)]
    rects = {
        m.group(1): tuple(map(int, m.groups()[1:]))
        for m in re.finditer(
            r"^\s*(\w+):\s*\{[^}]*?x:\s*(-?\d+),\s*y:\s*(-?\d+),\s*w:\s*(\d+),\s*h:\s*(\d+)",
            body, re.M,
        )
    }
    assert "temple" in rects, "the temple has no rectangle"
    tx, ty, tw, th = rects["temple"]
    for name, (x, y, w, h) in rects.items():
        if name == "temple":
            continue
        overlaps = tx < x + w and x < tx + tw and ty < y + h and y < ty + th
        assert not overlaps, f"the temple overlaps {name}"
