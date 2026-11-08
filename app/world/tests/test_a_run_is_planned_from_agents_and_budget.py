"""Two numbers in, a runnable scenario out - or a refusal before any spend.

The arithmetic here used to be done by hand before every run, and got wrong
at least once in a way that cost money: the first 20-resident day-2 scenario
was written to run two whole game-days on a $20 cap, which the measured rates
say costs $69. It ran, spent, and stopped less than a third of the way
through its own election.

So the planner's job is not really to write JSON. It is to say NO to a plan
whose vote is never reached, while saying it for free.
"""

from __future__ import annotations

import json

import pytest

from world.plan_run import CASTS, cast_for, plan, scenario
from world.scenario import Scenario

TICKS_PER_DAY = 144


def _plan(agents: int, budget: float, rate: float = 2.30, day: int = 2, election: bool = True):
    return plan(agents, budget, rate, day, election)


def test_twenty_residents_on_twenty_dollars_reaches_its_vote() -> None:
    p = _plan(20, 20.0)

    assert p["residents"] == 20
    assert p["fits"], "the run this whole file exists because of"
    assert p["counted_at"] < p["stop_tick"]


def test_fifty_residents_on_twenty_dollars_is_refused() -> None:
    """Refused for free, which is the point - the alternative is finding out
    by spending it."""
    p = _plan(50, 20.0)

    assert not p["fits"]
    assert p["counted_at"] >= p["stop_tick"]


def test_a_bigger_budget_buys_aftermath_not_a_longer_ballot() -> None:
    """Unclamped, $80 produced 13-hour windows: the town spent its whole
    life campaigning and the winners governed for nothing."""
    p = _plan(20, 80.0)

    assert p["window_hours"] <= 4.0
    assert p["stop_tick"] - p["counted_at"] > 100, (
        "the election ate the run instead of leaving time to govern"
    )


def test_windows_never_close_before_a_resident_can_act() -> None:
    """A 6-tick window is an election most of the town never sees, because a
    resident whose turn falls elsewhere never gets one inside it."""
    for agents, budget in ((50, 20.0), (30, 10.0), (20, 5.0)):
        assert _plan(agents, budget)["window_hours"] >= 1.5


def test_the_cast_rounds_down_never_up() -> None:
    """A run handed more residents than its budget was planned for runs out
    of money mid-election."""
    for asked in (21, 25, 29, 49):
        got, _ = cast_for(asked)
        assert got <= asked, f"asked {asked}, got a cast of {got}"

    assert cast_for(20)[0] == 20, "an exact size must be used exactly"
    assert cast_for(2)[0] == min(CASTS), "below the smallest cast, use it"


def test_every_cast_file_named_here_exists() -> None:
    """A planner that names a file nobody shipped refuses at boot instead,
    which is a worse place to find out."""
    from pathlib import Path

    app = Path(__file__).resolve().parents[2]
    for size, rel in CASTS.items():
        path = app / rel
        assert path.exists(), f"{rel} (for {size} residents) is not there"
        assert json.loads(path.read_text())["population"] == size, (
            f"{rel} does not hold {size} residents"
        )


def test_cost_scales_with_residents_and_days() -> None:
    """The one sum the whole thing rests on: twice the people, half the run."""
    twenty = _plan(20, 20.0)
    assert twenty["ticks"] == pytest.approx(
        20.0 / (20 * 2.30) * TICKS_PER_DAY, abs=1
    )

    assert _plan(20, 40.0)["ticks"] > _plan(20, 20.0)["ticks"]
    assert _plan(30, 20.0)["ticks"] < _plan(20, 20.0)["ticks"]


def test_what_it_writes_is_a_scenario_the_launcher_accepts(tmp_path) -> None:
    """The real contract. A plan that produces JSON `Scenario.load` rejects
    has moved the failure later, not removed it."""
    p = _plan(20, 20.0)
    path = tmp_path / "planned.json"
    path.write_text(json.dumps(scenario(p, "planned"), indent=2), encoding="utf-8")

    loaded = Scenario.load(str(path))

    assert loaded.world.budget_cap_usd == 20.0
    assert loaded.world.start_tick == p["start_tick"]
    assert loaded.election_phase_ticks()[3] == p["counted_at"], (
        "the planner and the charter disagree about when the vote is counted"
    )
    assert len(loaded.recording["views"]) == 6


def test_a_run_with_no_election_still_plans(tmp_path) -> None:
    """Not every run is about a vote."""
    p = _plan(20, 20.0, election=False)
    assert p["fits"]

    path = tmp_path / "quiet.json"
    path.write_text(json.dumps(scenario(p, "quiet"), indent=2), encoding="utf-8")
    assert Scenario.load(str(path)).world.stop_tick > p["start_tick"]


def test_it_starts_on_the_game_day_it_was_asked_for() -> None:
    """"Day 2 is when the election has to happen" - 144 ticks to a day, and
    dawn rather than midnight so the town wakes into it."""
    for day in (0, 1, 2, 3):
        p = _plan(12, 30.0, day=day)
        assert p["start_tick"] == day * TICKS_PER_DAY + 36
        assert p["counted_at"] // TICKS_PER_DAY == day, (
            "the vote drifted off the day it was planned for"
        )
