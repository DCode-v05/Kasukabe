"""A genesis file's `ablation_summary` must describe the cast in it.

`EVAL-16` noticed one instance: "`genesis_20.json`: 20 agents, all
`ablation_arm: full`; its `ablation_summary` says `{'full': 10, ...}` for a
20-agent file (stale)". Swept across the directory it is eleven of fifteen,
and worse than stale - `genesis_3.json` claims 314 residents' worth of arms
for three residents, because every subsetting generator copies the field
forward from the 314-cast it cut down and none recomputes it.

Why it matters beyond tidiness. The seam honours the PER-AGENT
`ablation_arm` (`engine/src/sim/pinned_boot.py`), so no run has ever
behaved wrongly because of this. What reads the summary is a person, and
the evaluation spec's L4 arm comparison: a file that says it has 20
no-learning residents when it has none is a claim about an experiment that
was never run. `make_genesis_20.py` recomputes `population`, `households`,
`small_businesses` and `usd_ledger` when it doubles the cast and simply
forgets this one field, which is why it drifted quietly.

The invariant is arithmetic, so it is asserted over every committed file
rather than over the one that was noticed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

GENESIS_DIR = Path(__file__).resolve().parents[1]
GENESIS_FILES = sorted(GENESIS_DIR.glob("genesis*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_are_genesis_files_to_check() -> None:
    """A glob that matches nothing would make every test below vacuous."""
    assert GENESIS_FILES, f"no genesis*.json under {GENESIS_DIR}"


@pytest.mark.parametrize("path", GENESIS_FILES, ids=lambda p: p.name)
def test_the_summary_totals_the_population(path: Path) -> None:
    genesis = _load(path)
    summary = genesis.get("ablation_summary")
    if summary is None:
        pytest.skip("no ablation_summary in this file")
    counted = sum(v for v in summary.values() if isinstance(v, int))
    agents = len(genesis.get("agents") or [])
    assert counted == agents, (
        f"{path.name} says {counted} residents across its arms and holds {agents}"
    )


@pytest.mark.parametrize("path", GENESIS_FILES, ids=lambda p: p.name)
def test_the_summary_matches_the_arms_the_agents_actually_carry(path: Path) -> None:
    """The stronger form. Totalling right while naming the wrong arms would
    still describe an experiment nobody ran."""
    genesis = _load(path)
    summary = genesis.get("ablation_summary")
    if summary is None:
        pytest.skip("no ablation_summary in this file")

    actual: dict[str, int] = {}
    for agent in genesis.get("agents") or []:
        arm = str(agent.get("ablation_arm") or "full")
        actual[arm] = actual.get(arm, 0) + 1

    for arm, claimed in summary.items():
        if not isinstance(claimed, int):
            continue
        assert claimed == actual.get(arm, 0), (
            f"{path.name} claims {claimed} in arm {arm!r}; the agents carry {actual.get(arm, 0)}"
        )
