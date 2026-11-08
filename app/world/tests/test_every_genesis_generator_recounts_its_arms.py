"""Every builder that writes a genesis file must recount the arms it wrote.

The companion to `test_the_genesis_summary_counts_the_cast.py`. That one
holds the committed FILES to the invariant; this one holds the four
BUILDERS to it, so a repaired file cannot quietly rot the next time
somebody regenerates it.

Written after the fix for this broke one of the four. `make_genesis_20`
and `make_genesis_50` end in `return genesis`, so a recount could be
inserted before it; `make_genesis_neighbours.build()` ended in
`return {...}` - a dict literal with no variable - and the same edit
produced an unreachable line referencing a name that did not exist, while
blanking the summary it was meant to fix. `importlib.import_module` did
not catch it, because a NameError in dead code fires at call time and
never. Calling `build()` is the only thing that would have.
"""
from __future__ import annotations

import pytest


def _check(genesis: dict, who: str) -> None:
    summary = genesis.get("ablation_summary")
    assert summary, f"{who} wrote no ablation_summary"
    counted = sum(v for v in summary.values() if isinstance(v, int))
    agents = genesis.get("agents") or []
    assert counted == len(agents), (
        f"{who} claims {counted} residents across its arms and built {len(agents)}"
    )
    actual: dict[str, int] = {}
    for agent in agents:
        arm = str(agent.get("ablation_arm") or "full")
        actual[arm] = actual.get(arm, 0) + 1
    for arm, claimed in summary.items():
        if isinstance(claimed, int):
            assert claimed == actual.get(arm, 0), (
                f"{who} claims {claimed} in arm {arm!r}, built {actual.get(arm, 0)}"
            )


def test_make_genesis_20_recounts() -> None:
    from world.make_genesis_20 import build

    _check(build(), "make_genesis_20.build()")


def test_make_genesis_50_recounts() -> None:
    from world.make_genesis_50 import build

    _check(build(), "make_genesis_50.build()")


def test_make_genesis_neighbours_recounts() -> None:
    """The one the first fix broke. `build()` must be CALLED - importing
    the module leaves a NameError in unreachable code undiscovered."""
    from world.make_genesis_neighbours import build

    _check(build(), "make_genesis_neighbours.build()")


@pytest.mark.parametrize("want", [3, 6, 30])
def test_build_subset_recounts(want: int) -> None:
    from world.make_genesis_subset import build_subset

    _check(build_subset(want), f"build_subset({want})")
