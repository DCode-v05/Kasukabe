"""The names one township module reaches for exist in another.

`UI-26`. `shell.js` wraps `window.openModal`, `closeModal`, `selectPlace`,
`hidePlaceCard`, `showEnginePanel`, `hideEnginePanel` and `selectAgent`;
`live-cast.js` calls `window.renderScoreboard` and four more. None of
those are declared anywhere as an interface. They work because
`township-v2.js` is a CLASSIC script and a classic script's top-level
`function` declarations become properties of `window`.

Every one of those call sites is guarded with
`typeof window[name] === 'function'`, which is careful and is also the
problem: wrapping `township-v2.js` in an IIFE, or serving it as an ES
module, would make every guard fall through silently. The drawer would
stop opening, the scoreboard would stop being nudged, and nothing
anywhere would error.

So the contract is written down here. A name that disappears fails this
file instead of quietly turning the shell into decoration.

(The review also said `live-cast.js`'s comment on this "gets it
backwards". It does not: top-level `function` declarations and `var` do
become `window` properties in a classic script, while `const`/`let` go to
the global lexical environment and do not. The comment says exactly that
and is correct. The fragility is real; the comment is not the cause.)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TOWNSHIP_DIR = Path(__file__).parent.parent / "static" / "js" / "township"
PROVIDER = TOWNSHIP_DIR / "township-v2.js"

EXPECTED = {
    "shell.js": [
        ("openModal", "the resident file never opens"),
        ("closeModal", "the right-hand drawer never closes with the panel"),
        ("selectPlace", "clicking a building does not open the dock"),
        ("hidePlaceCard", "the dock stays open over nothing"),
        ("showEnginePanel", "the Real sim readout does not open the dock"),
        ("hideEnginePanel", "the dock stays open after the readout closes"),
        ("selectAgent", "clicking a sprite does not open the resident file"),
    ],
    "live-cast.js": [
        ("renderScoreboard", "the scoreboard keeps the pre-prune cast"),
        ("refreshOpenSkillsTab", "an open Skills tab shows the old resident"),
    ],
}


@pytest.fixture(scope="module")
def provided() -> set[str]:
    """Every name `township-v2.js` puts on `window`.

    Two ways, both real in a classic script: a top-level `function` (which
    the language does automatically) and an explicit `window.x =`.
    """
    source = PROVIDER.read_text(encoding="utf-8")
    return (
        set(re.findall(r"^function\s+([A-Za-z_$][\w$]*)\s*\(", source, re.M))
        | set(re.findall(r"^window\.([A-Za-z_$][\w$]*)\s*=", source, re.M))
    )


@pytest.mark.parametrize(
    "consumer,name,cost",
    [(c, n, why) for c, names in EXPECTED.items() for n, why in names],
)
def test_the_name_a_module_reaches_for_exists(provided, consumer, name, cost) -> None:
    assert name in provided, (
        f"UI-26: {consumer} calls window.{name} and township-v2.js does not "
        f"provide it. Every call site is guarded with a typeof check, so this "
        f"does not throw - it silently does nothing, and {cost}"
    )


@pytest.mark.parametrize("consumer", sorted(EXPECTED))
def test_the_consumer_still_reaches_for_these(consumer) -> None:
    """The other direction. A list of expectations nobody holds any more is
    a test that passes while protecting nothing."""
    source = (TOWNSHIP_DIR / consumer).read_text(encoding="utf-8")
    for name, _cost in EXPECTED[consumer]:
        assert name in source, (
            f"{consumer} no longer mentions {name} - remove it from EXPECTED "
            "rather than leaving a guard over a contract nobody uses"
        )


def test_township_v2_is_still_a_classic_script() -> None:
    """The whole arrangement rests on this. An `export` or a top-level
    IIFE would move every one of those functions out of `window`, and
    every guarded call site would fall through without a word."""
    source = PROVIDER.read_text(encoding="utf-8")
    assert not re.search(r"^\s*export\s", source, re.M), (
        "UI-26: township-v2.js is now an ES module, so its top-level "
        "functions are no longer on `window` - shell.js's drawer wiring and "
        "live-cast.js's panel nudges are now silent no-ops"
    )
    page = (TOWNSHIP_DIR.parent.parent / "township-v2.html").read_text(encoding="utf-8")
    tag = re.search(r"<script[^>]*township-v2\.js[^>]*>", page)
    assert tag, "township-v2.js is not loaded by the page at all"
    assert 'type="module"' not in tag.group(0), (
        "UI-26: the page now loads township-v2.js as a module, which takes "
        "its functions off `window` and silently unhooks shell.js"
    )
