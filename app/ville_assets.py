"""One gate for every test that needs the Ville tileset.

`GROW-27`. The tileset is a gitignored external asset directory, and the
tests that need it behaved in two different wrong ways when it was
absent: `world/map/tests/test_the_town_is_walkable.py` FAILED - five hard
failures on `town.movement.world_map` being `None`, which reads exactly
like a broken map - while the end-to-end construction chain in
`world/tools/tests/test_construction.py` SKIPPED, silently, one character
in a field of dots.

The silent skip is the dangerous one. A suite reporting "1,847 passed"
when the only proof that a resident can petition, be granted a plot,
build, and have the building become a real map sector never ran, is a
suite saying the chain works when nobody checked.

So every such test calls `require_ville_assets` with a plain-language
description of what it is the evidence FOR, and `app/conftest.py`'s
terminal hook prints those descriptions at the end of any run that could
not check them.

This lives beside conftest.py rather than inside it because pytest
resolves the bare name `conftest` to the nearest package-local
conftest.py, so `from conftest import ...` inside `world/map/tests/`
finds that directory's own file instead of this one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

VILLE_DIR = (
    Path(__file__).resolve().parents[1]
    / "smallville"
    / "environment"
    / "frontend_server"
    / "static_dirs"
    / "assets"
    / "the_ville"
)

SKIPPED_FOR_ASSETS: set[str] = set()


def ville_assets_present() -> bool:
    return VILLE_DIR.exists()


def require_ville_assets(what: str) -> Path:
    """Skip - never fail - when the tileset is not mounted, and remember
    that `what` went unproven.

    `what` is a sentence about the world, not a test name: "the town is
    one connected walkable place" is what the summary needs to print,
    "test_the_map_is_one_connected_place" is not.
    """
    if not VILLE_DIR.exists():
        SKIPPED_FOR_ASSETS.add(what)
        pytest.skip(
            f"Ville asset directory not found at {VILLE_DIR} - cannot prove: {what}"
        )
    return VILLE_DIR
