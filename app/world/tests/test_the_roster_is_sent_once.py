"""The town roster is written once, not re-sent every tick.

`CORE-16`. `standing_brief` returned every living resident's name and
trade on every tick, for every resident. That is O(residents) tokens per
resident per tick for a list that did not change between two ticks in any
run this project has made: about 310 tokens a tick on the 50-agent cast
and roughly 1,550 at the 250-resident design target, which over one
144-tick game day is tens of millions of tokens spent re-stating the same
names.

It cannot simply be deleted. The roster exists because of an observed
failure - without it a resident looking for a buyer reached for whatever
names were within reach, which were the ones written on the map's
buildings, a cast of thirty who are not in this run. Tobias spent a
morning hailing "Harvey" and "Isabella"; Bram walked to "sh_silva". So it
moves to the once-written persona seed, and only for residents that seed
actually reached - and what a snapshot cannot know, arrivals and deaths,
still rides the tick.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest_asyncio.fixture
async def runner(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    return runner


def _two(runner) -> tuple[str, str]:
    ids = list(runner.world_state.agents)
    return ids[0], ids[1]


def test_an_unseeded_resident_still_gets_the_whole_roster(runner) -> None:
    """Mock mode writes no persona at all. Dropping the roster from the
    brief is only safe where it is provably somewhere else, so here it
    must still be in the brief - otherwise the fix reintroduces the
    failure the roster was added to prevent."""
    who, _other = _two(runner)
    assert runner.standing_brief(who)["townspeople"], (
        "CORE-16: this resident was never seeded and its brief no longer "
        "names anybody - it has no way to learn who exists"
    )


def test_a_seeded_resident_is_not_told_twice(runner) -> None:
    who, _other = _two(runner)
    runner.roster_seeded.add(who)
    brief = runner.standing_brief(who)
    assert brief["townspeople"] == []
    assert brief["household"] == []


def test_the_seed_carries_the_same_names(runner) -> None:
    """What is dropped from the tick must be present in the seed, in the
    same form: "Name (trade)"."""
    who, other = _two(runner)
    text = runner.town_roster_text(who)
    entry = runner.roster_entry(other)
    assert entry and entry in text
    assert "THE PEOPLE OF THIS TOWN" in text


def test_the_roster_names_nobody_who_is_dead(runner) -> None:
    who, other = _two(runner)
    runner.world_state.agents[other].alive = False
    assert runner.roster_entry(other) not in runner.town_roster_text(who)


def test_the_roster_says_nothing_about_where_anyone_is(runner) -> None:
    """Names and trades only. A roster carrying positions would hand every
    resident a live map of the whole town for free, which is what
    perception and `look_around` are for."""
    who, _other = _two(runner)
    text = runner.town_roster_text(who)
    for word in ("location", "tile", "standing", "is at "):
        assert word not in text.lower()


# ── what a snapshot cannot know ────────────────────────────────────────


def test_a_resident_born_later_is_still_announced(runner) -> None:
    """The failure the roster prevents, arriving by a different door: a
    resident born on day three is in nobody's persona.txt."""
    who, other = _two(runner)
    runner.roster_seeded.add(who)
    runner.roster_seen_by[who] = {
        a for a in runner.world_state.agents if a != who and a != other
    }
    changes = runner.standing_brief(who)["roster_changes"]
    assert any("been born" in line for line in changes), (
        f"CORE-16: a new resident exists and nobody was told. Got: {changes}"
    )
    assert runner.agent_names[other] in " ".join(changes)


def test_a_death_is_announced_too(runner) -> None:
    """Otherwise the dead stay in everybody's roster forever."""
    who, other = _two(runner)
    runner.roster_seeded.add(who)
    runner.roster_seen_by[who] = {a for a in runner.world_state.agents if a != who}
    runner.world_state.agents[other].alive = False

    changes = runner.standing_brief(who)["roster_changes"]
    assert any("has since died" in line for line in changes)
    assert runner.agent_names[other] in " ".join(changes)


def test_nothing_changed_says_nothing(runner) -> None:
    who, _other = _two(runner)
    runner.roster_seeded.add(who)
    runner.roster_seen_by[who] = {a for a in runner.world_state.agents if a != who}
    assert runner.standing_brief(who)["roster_changes"] == []


def test_an_unseeded_resident_gets_no_deltas(runner) -> None:
    """Its brief already carries the live list, so a delta would be the
    same news twice."""
    who, _other = _two(runner)
    assert runner.standing_brief(who)["roster_changes"] == []
