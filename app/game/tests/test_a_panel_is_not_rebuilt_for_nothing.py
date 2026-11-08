"""A panel whose contents have not changed is not rebuilt.

`UI-29`. `renderPlaceCard` ran every 400 ms while a place was selected,
rebuilding its innerHTML and binding one click listener per person row;
`renderOccupancy`, `renderGovernment` and `renderWork` ran on the same
400 ms tick, `renderScoreboard` every 1.2 s over every agent with a
`balanceNow` per row, and `refreshShopSigns` every second. On the
50-agent cast this is affordable. The design target is 250 residents with
People cards and it is not.

The cost that matters is not building the string - it is assigning it.
`innerHTML =` makes the browser tear down and re-parse the subtree, drop
any text selection inside it, reset its scroll position, and orphan every
listener bound to the old nodes. Most frames produce a string identical
to the one already on screen.

`paint()` compares the OUTPUT rather than fingerprinting the inputs,
which is the deliberate choice: a fingerprint must be kept in step by
hand with everything a renderer reads, and the first field somebody
forgets becomes a panel that silently stops updating.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TOWNSHIP = Path(__file__).parent.parent / "static" / "js" / "township" / "township-v2.js"
HARNESS = Path(__file__).parent / "ui29_paint_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH on this host"
)


@pytest.fixture(scope="module")
def source() -> str:
    return TOWNSHIP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def painted() -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_sixteen_frames_of_the_same_panel_cost_two_writes(painted: dict) -> None:
    """The finding, as a number. Ten identical frames, one change, five
    more identical: two DOM writes, not sixteen."""
    assert painted["writes"] == 2, (
        f"UI-29: {painted['writes']} innerHTML assignments for two distinct "
        "panel contents - the 400 ms loop is re-parsing an unchanged subtree"
    )


def test_the_panel_still_updates_when_it_changes(painted: dict) -> None:
    """Half a fix is worse than none: a cache that never invalidates is a
    panel frozen on the first frame."""
    assert painted["html"] == "<div>different</div>"
    assert painted["returns"][0] is True
    assert painted["returns"][10] is True, "the change was swallowed by the cache"


def test_it_reports_whether_it_wrote(painted: dict) -> None:
    """`renderPlaceCard` uses the return value to decide whether to rebind
    its per-row click listeners."""
    assert painted["returns"][1] is False
    assert painted["returns"][-1] is False


@pytest.mark.parametrize(
    "renderer",
    ["renderGovernment", "renderScoreboard", "renderWork", "renderPlaceCard",
     "renderEnginePanel", "renderOccupancy"],
)
def test_each_hot_renderer_paints(source: str, renderer: str) -> None:
    body = source[source.index(f"function {renderer}(") :][:20_000]
    body = body[: body.index("\n}\n")]
    assert "paint(el," in body, f"UI-29: {renderer} still assigns innerHTML directly"
    assert not re.search(r"\bel\.innerHTML\s*=", body), (
        f"UI-29: {renderer} bypasses paint(), so its element's cache would "
        "describe content that is no longer on screen"
    )


def test_both_writers_of_the_place_card_share_the_cache(source: str) -> None:
    """`placeCard` is written by renderPlaceCard AND renderEnginePanel. A
    writer that bypassed paint() would leave `_painted` describing the
    other panel's content, and the next render would skip - showing the
    engine readout under a place card's heading."""
    for renderer in ("renderPlaceCard", "renderEnginePanel"):
        body = source[source.index(f"function {renderer}(") :][:20_000]
        assert "paint(el," in body[: body.index("\n}\n")]


def test_the_listener_rebinding_is_gated(source: str) -> None:
    body = source[source.index("function renderPlaceCard(") :]
    body = body[: body.index("\n}\n")]
    bind_at = body.index("addEventListener")
    assert "if (!wrote) return;" in body[:bind_at], (
        "UI-29: the place card rebinds a click listener per person row on "
        "every 400 ms frame, onto nodes that are about to be replaced"
    )


def test_the_shop_signs_are_gated(source: str) -> None:
    body = source[source.index("function refreshShopSigns(") :]
    body = body[: body.index("\n}\n")]
    assert "_lastShopSigns" in body, (
        "UI-29: refreshShopSigns hands the renderer an identical badge map "
        "every second"
    )
    assert body.index("if (key === _lastShopSigns) return;") < body.rindex("V.setLabelBadges(badges)")
