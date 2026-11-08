"""With a world bound, the page does not speak over it.

`UI-04`. `township-v2.js` is a ~3,000-line in-browser simulation of a town
- wages, guard rosters, a hunger economy, births, a cabinet - written to
make an UNBOUND town readable, and it kept running over a real one. The
review lists the contradictions: the Government book named five
hash-picked clerks as Prime Minister and ministers, with sashes on the
map, while `/governance/government` knew the real seats; the wealth
leaderboard ranked residents by this file's `WAGE` table while the
world's `sim_balance` sat unread on the same poll; People cards billed
engine agents "wage +125 - upkeep -55 - tax -30 / day" and "unlocks day
3"; shop signs said SOLD OUT from `foodSim` while `vitals.js` showed the
world's real holdings two panels away; `stepBirths` pushed `b0001 ...`
into `AGENTS` and gave them People cards; `retrade`/`staffThePosts`
rewrote `a.occ`, so a genesis day_laborer became a police officer on the
card, the place card and the map costume.

The finding names its own fix: "`activityFor` and `dayPlaceLineFor`
already show the pattern that fixes it (world wins when bound)". That is
what this applies, at each chokepoint rather than at each of the 77 sites
that read `a.occ`.

WHAT THIS DOES NOT DO. The finding's stated fix is also a SPLIT - `live/`
modules beside an optional `demo/` bundle, with `healthSim`, `foodSim`,
`stepBirths`, `seatHolders`, `WAGE`, `INITIATIVES`, `retrade`,
`staffThePosts` and `LINES` deleted. Whether the file:// showcase survives
at all is a product decision (operator decision 12) and not one a test may
make, so the model is left intact and silenced instead of removed. Every
one of those functions is still here and still runs with no world bound -
which is the only condition it was ever true under.

The harness runs the REAL functions out of the shipped file. Grepping the
source for `isBound(` would pass while every one of them still printed
the model's answer.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
TOWNSHIP = HERE.parent / "static" / "js" / "township" / "township-v2.js"
HARNESS = HERE / "ui04_world_wins_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH on this host"
)


@pytest.fixture(scope="module")
def ran() -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def source() -> str:
    return TOWNSHIP.read_text(encoding="utf-8")


# -- the cabinet ---------------------------------------------------------

def test_the_seats_are_the_worlds(ran: dict) -> None:
    """The world's PM is a0002 - a day_laborer, whom the hash would never
    have picked. The model's cabinet is a0001 first. One of these is a
    fact about the run."""
    assert ran["bound"]["seats"] == ["Prime Minister:a0002"], (
        "UI-04: the Government book is still filling its own seats over a "
        "bound world, so the map puts a sash on someone nobody elected"
    )


def test_a_bound_town_with_no_election_has_no_ministers(ran: dict) -> None:
    """The honest answer, and the one that took a decision. A town of 20
    has no cabinet before it votes; that is a finding about the run, and
    filling the five seats to avoid an empty panel destroys it."""
    assert ran["boundNoGovernment"]["seats"] == []


def test_an_unbound_town_still_has_a_government(ran: dict) -> None:
    """The model is silenced, not deleted. With nothing bound it is the
    only thing that makes the town readable, and the file:// showcase runs
    on exactly this path."""
    assert ran["unbound"]["seats"] == ["Prime Minister:a0001", "Minister:a0002"]


def test_the_title_follows_the_seat(ran: dict) -> None:
    """`titleOf` feeds the place card, the search box and the roster
    filter. a0001 is a government clerk the model would have crowned."""
    assert ran["bound"]["boundTitle"] == "government clerk"
    assert ran["unbound"]["boundTitle"] == "Prime Minister"


# -- money ---------------------------------------------------------------

def test_a_bound_balance_is_the_worlds(ran: dict) -> None:
    """The world says this resident holds ZERO. The model says 999,999.
    Zero is the case that matters: a truth test would have taken the
    model's answer for precisely the resident whose poverty is the
    interesting fact."""
    assert ran["bound"]["boundBalance"] == 0, (
        "UI-04: the wealth leaderboard is still ranking the WAGE table, so "
        "every money figure on a live run is a property of this file's "
        "constants rather than of the town"
    )


def test_an_unbound_resident_in_a_bound_town_keeps_the_model(ran: dict) -> None:
    """A connected world says nothing about a resident whose id is not in
    its genesis - a subset run renumbers the town. Those must keep the
    model or they freeze."""
    assert ran["bound"]["unboundBalance"] == 999999


def test_the_usd_meter_is_the_worlds(ran: dict) -> None:
    """$2.00 wallet, 40 cents left = $1.60 spent. The model's decay curve
    says $0.90 at the same instant, on the same card."""
    assert ran["bound"]["boundSpend"] == pytest.approx(1.60)
    assert ran["unbound"]["boundSpend"] == pytest.approx(0.9024, abs=1e-3)


# -- births --------------------------------------------------------------

def test_a_bound_town_invents_no_children(ran: dict) -> None:
    """`b0001` in `AGENTS` is a resident the world has never heard of, and
    `POP`, the census bars and every per-capita figure move with them.
    The birth chance is forced to 1.0 in the harness, so a model that ran
    at all would show here."""
    assert ran["bound"]["newborns"] == 0, (
        "UI-04: stepBirths is still pushing b0001... into a live cast"
    )
    assert ran["unbound"]["newborns"] == 2, (
        "the model was removed rather than silenced - with no world bound "
        "the town no longer grows at all"
    )


# -- skills --------------------------------------------------------------

def test_the_skill_line_is_what_was_practised(ran: dict) -> None:
    assert ran["bound"]["skillLine"] == "Baking"


# -- the acceptance grep, at the source ----------------------------------

def test_every_fiction_the_finding_names_is_gated(source: str) -> None:
    """The finding's acceptance is a DOM grep for `unlocks day`, `Prime
    Minister`, `starvation` and `b0001` on a bound run. The harness above
    proves the data behind each; this checks no ungated site emits them.

    Each entry is (what the DOM must not show, the gate that must be
    above it).
    """
    lines = source.splitlines()
    # Literal DOM text, not a loose pattern. An earlier draft matched
    # "day ' +" and caught `makeNewborn`'s bio prose - which is gated one
    # level up by `stepBirths`, so the test was reporting a site that was
    # already correct. A guard that cries wolf gets its assertion widened
    # until it proves nothing.
    for phrase, emitter in (
        ("unlocks day", "isBound(a)"),
        ("\U0001f512 day ", "isBound(a)"),
        ("wage +", "isBound(a)"),
    ):
        for index, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if phrase not in line or stripped.startswith(("//", "*", "/*")):
                continue
            window = "\n".join(lines[max(0, index - 14):index])
            assert emitter in window, (
                f"UI-04: line {index} emits {phrase!r} with no {emitter} "
                "above it, so a bound resident is shown the model's answer:"
                f"\n    {stripped}"
            )


def test_the_town_wide_models_stand_down(source: str) -> None:
    """`stepHealthDay`, `stepFoodDay` and `stepBirths` are not per-resident,
    so they turn on whether a world is bound at all."""
    for name in ("stepHealthDay", "stepFoodDay", "stepBirths"):
        start = source.index("function " + name + "(")
        assert "worldIsBound()" in source[start:start + 1400], (
            f"UI-04: {name} still runs over a bound town - the shop signs "
            "say SOLD OUT beside the world's real holdings, and residents "
            "the world is still dispatching to are logged dead of starvation"
        )


def test_the_model_is_silenced_not_deleted(source: str) -> None:
    """The split into live/ and demo/ is operator decision 12, not a
    decision this pass may take. If somebody does take it, this test is
    what tells them these are the pieces the finding lists."""
    for name in ("healthSim", "foodSim", "stepBirths", "seatHolders",
                 "WAGE", "retrade", "staffThePosts"):
        assert name in source, (
            f"UI-04: {name} is gone. If the demo bundle was split out, this "
            "test has done its job and should go with it; if it was deleted "
            "outright, the file:// showcase no longer runs."
        )
