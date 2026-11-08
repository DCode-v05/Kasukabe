"""`move` is two mechanisms, and the resident is now told which it got.

`TOOL-05`. A walk happens only when the destination is a map sector AND
the agent's position resolves to a tile; everything else is an opaque
jump. That means every `home:*`, `business:*`, workshop and
resource-family id is a jump, and so is the FIRST move of every run,
because a genesis resident starts at a `home:*` with no tile. Run 23:
170 `look_around` results with `"tile": null` against 3 with a tile.

The two returned indistinguishable shapes. A walk gave `walking: true`
and `estimated_ticks`; a jump gave `{"location_id": X}` and nothing
else, so a model read teleport and walk as the same act - and then read
`"tile": null` from `look_around` with nothing anywhere explaining why.

Four of the finding's five fixes are held here:

* **(a)** an arrival carries `target_agent_id`, so it WAKES the
  resident. `default_interrupt_policy` interrupts the scheduler on that
  key and `agent.moved` carried `agent_id` only, so arriving anywhere
  never woke anybody - a resident walked ten ticks and then waited for
  its next scheduled turn to notice it had got there;
* **(b)** `agent.moved` renders as words. It arrived as
  `- [agent.moved, nearby] {...}`, the same reverse-engineer-it-from-a-
  dict defect `format_heard_event`'s docstring makes the case about for
  speech;
* **(c)** a jump says it is one, in the resident's own terms;
* **(d)** `rest` no longer throws away a journey unless asked.

Not done, and named rather than implied: (e), seeding `agent_tiles` at
genesis from each home's entry point so the first move of a run is a
real walk. That is a genesis change and it moves what every existing run
comparison is measured against.
"""
from __future__ import annotations

import pytest

from world.retrieval_depth import format_heard_event
from world.tools.tests.conftest import spawn_agent
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map


class _Seen:
    """A PerceivedEvent's shape, without importing the whole perception."""

    def __init__(self, payload, for_agent_id=None, involves_self=False):
        self.type = "agent.moved"
        self.tick = 10
        self.payload = payload
        self.for_agent_id = for_agent_id
        self.involves_self = involves_self


# ── (c) a jump says it is a jump ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_jump_does_not_look_like_a_walk(world, registry) -> None:
    await spawn_agent(world, "a1", "home:hh-1")

    result = await registry.execute(world, "a1", "move", {"destination_location_id": "mill"})

    assert result.result["walked"] is False
    assert result.result["walking"] is False
    assert "no route on the map" in result.result["note"], (
        "the resident is told nothing about why this took no time"
    )


@pytest.mark.asyncio
async def test_a_real_walk_says_so(world, registry) -> None:
    await spawn_agent(world, "a1", "Hobbs Cafe")

    result = await registry.execute(
        world, "a1", "move", {"destination_location_id": "Kasukabe Farmland"}
    )

    assert result.result["walking"] is True
    assert result.result["estimated_ticks"] >= 1
    assert "walked" not in result.result, (
        "a journey that has not started has not walked anywhere yet"
    )


# ── (a) arriving wakes you ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_jump_wakes_the_resident_that_made_it(world, registry) -> None:
    await spawn_agent(world, "a1", "home:hh-1")

    result = await registry.execute(world, "a1", "move", {"destination_location_id": "mill"})

    moved = [e for e in result.events if e.type == "agent.moved"]
    assert moved, "the jump published no event"
    assert moved[0].payload["target_agent_id"] == "a1", (
        "TOOL-05(a): default_interrupt_policy reads target_agent_id, so "
        "without it arriving somewhere never woke anybody"
    )


def test_only_the_arrival_wakes_not_every_tile() -> None:
    """One wake per journey. One per TILE is the 21 GB."""
    import inspect

    from world.core import movement as movement_module

    source = inspect.getsource(movement_module)
    marker = source.index('payload["target_agent_id"] = agent_id')
    guard = source.rindex("if arrived:", 0, marker)
    assert marker - guard < 800, (
        "the wake is not inside the `if arrived` branch, so every tile "
        "crossed would interrupt the scheduler"
    )


# ── (b) an arrival reads as an arrival ───────────────────────────────


def test_your_own_arrival_is_a_sentence() -> None:
    line = format_heard_event(
        _Seen({"agent_id": "a1", "location_id": "Hobbs Cafe", "arrived": True}, "a1"),
        {"a1": "Marta"},
    )
    assert line == "  - You arrived at Hobbs Cafe."


def test_your_own_jump_says_the_place_is_not_on_the_map() -> None:
    line = format_heard_event(
        _Seen(
            {"agent_id": "a1", "location_id": "mill", "arrived": True, "walked": False},
            "a1",
        ),
        {"a1": "Marta"},
    )
    assert "stepped straight to mill" in line
    assert "not on the map" in line


def test_mid_walk_you_are_told_you_are_still_walking() -> None:
    line = format_heard_event(
        _Seen(
            {"agent_id": "a1", "location_id": "sector:900002", "arrived": False}, "a1"
        ),
        {"a1": "Marta"},
    )
    assert "still" not in line  # it is not a repeat of the goal
    assert "passing sector:900002" in line


def test_somebody_elses_arrival_names_them() -> None:
    line = format_heard_event(
        _Seen({"agent_id": "a2", "location_id": "Hobbs Cafe", "arrived": True}, "a1"),
        {"a2": "Arjun Holloway"},
    )
    # `_named` carries the id beside the name, so `say` has something to
    # address - the same shape every other rendered event uses.
    assert "Arjun Holloway (a2) arrived at Hobbs Cafe." in line


def test_somebody_else_crossing_a_tile_is_not_worth_a_line() -> None:
    """Nine neighbours mid-walk would fill the whole `Recently:` window
    and push a question addressed to this resident off the end."""
    assert format_heard_event(
        _Seen({"agent_id": "a2", "location_id": "sector:900002", "arrived": False}, "a1"),
        {"a2": "Arjun"},
    ) == ""


def test_an_event_that_does_not_say_is_still_shown() -> None:
    """Absent means "not stated", not "no". A producer that sets neither
    key must not have its event silently dropped."""
    line = format_heard_event(
        _Seen({"agent_id": "a2", "location_id": "Hobbs Cafe"}, "a1"), {"a2": "Arjun"}
    )
    assert line, "an agent.moved with no `arrived` key vanished"
