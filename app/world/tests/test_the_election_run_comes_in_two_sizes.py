"""The election run comes in two sizes, and both casts are ready for it.

`run-election.sh 20` is the laptop run; `run-election.sh 50` is the team
box's (docs/TEAM_BOX_GUIDE.md), with the cast of the 50-resident run of
2026-09-16 so the two runs can be compared. Each size has a funded genesis:
$14 of thinking per resident, because a resident spends $8.70-10.90 reaching
the run's end at tick 590, and the $1,000 invariant kept by the treasury.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]
WORLD = APP / "world"


@pytest.mark.parametrize("size, adults, treasury", [(20, 16, 72_000), (50, 34, 30_000)])
def test_each_cast_is_funded_and_counted(size, adults, treasury) -> None:
    genesis = json.loads((WORLD / f"genesis_{size}_funded.json").read_text(encoding="utf-8"))
    agents = genesis["agents"]
    ledger = genesis["usd_ledger"]

    assert len(agents) == size
    assert {a["usd_wallet_cents"] for a in agents} == {1400}
    assert ledger["treasury_cents"] == treasury
    assert sum(a["usd_wallet_cents"] for a in agents) + ledger["treasury_cents"] == 100_000
    assert sum(1 for a in agents if a["age_years"] >= 18) == adults


def test_the_fifty_are_the_fifty_of_the_earlier_run() -> None:
    """Same people as genesis_50.json, so the two runs are comparable."""
    ids = lambda name: sorted(a["agent_id"] for a in json.loads((WORLD / name).read_text())["agents"])
    assert ids("genesis_50_funded.json") == ids("genesis_50.json")


def _launch(*args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(APP / "run-election.sh"), *args], cwd=APP,
                          env={**os.environ, **env}, capture_output=True, text=True, timeout=30)


def test_no_size_but_twenty_or_fifty() -> None:
    result = _launch("30")

    assert result.returncode == 2 and "20 or 50" in result.stdout


def test_the_key_gate_is_gone_and_the_reason_is_written_down() -> None:
    """The launcher used to refuse without `CONFIRM_KEY_ROTATED=1`.

    It never checked a key - no gate here can, without spending money. It
    only made the operator say the words, and twice the words were untrue:
    `docs/runs/series/election8/KEY-NOT-ROTATED.txt` and `civic12`'s copy
    both record a run started on the key pasted into a chat on 2026-09-21,
    with the flag set anyway.

    A gate that is routinely answered wrong teaches people to answer gates
    wrong, and it was removed for that reason rather than quietly deleted.
    This test holds the launcher to the removal AND to the explanation:
    the next reader who finds `KEY-NOT-ROTATED.txt` in the run evidence
    must be able to find out what happened to the gate that produced it.
    """
    script = (APP / "run-election.sh").read_text(encoding="utf-8")

    # The expansion, not the bare name: the comment block that explains the
    # removal has to say what was removed, and a test that cannot tell an
    # explanation from a gate would forbid writing one down.
    assert "${CONFIRM_KEY_ROTATED" not in script, (
        "the key gate is back - if that is deliberate, this test and the "
        "comment block explaining the removal need revisiting together"
    )
    assert "exit 2" in script, "the size check should still refuse"
    assert "KEY-NOT-ROTATED" in script, (
        "the removal is unexplained. The run evidence still carries "
        "KEY-NOT-ROTATED.txt files; a launcher that does not say why the "
        "gate went leaves them looking like a gate somebody forgot"
    )


def test_run20_still_means_the_twenty_resident_run() -> None:
    wrapper = (APP / "run20.sh").read_text(encoding="utf-8")
    assert 'run-election.sh" 20' in wrapper
