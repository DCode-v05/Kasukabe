"""A resident may work "until dusk", and the town survives it.

`ContinuingAction.satisfied` has called `world.is_daytime()` since it was
written (`state.py:377`) and `WorldState` had no such method. Any resident
that committed to a job with an `until={"dusk": ...}` stop condition took
the WHOLE TICK LOOP down with an AttributeError the moment
`advance_continuing` reached it.

It was not caught by any test because no test ever built a continuing
action with a `dusk` stop - every fixture used `units`, `ticks` or
`item_count`. It was caught by a real run: eight minutes into the
50-agent series on 2026-09-08, `run_genesis.py:6656` raised it out of
`dispatch_due_agents` and every engine died. The spend was still climbing
at $10.77/hour while the town was no longer being simulated.

The three other stop conditions are tested here too, because the reason
this one was missing is that nothing exercised the branch table.
"""

from __future__ import annotations

import pytest

from world.core.clock import timestamp_for
from world.tools.state import ContinuingAction


def _action(**until) -> ContinuingAction:
    return ContinuingAction(
        tool="harvest",
        params={"item": "wheat"},
        until=until,
        started_tick=0,
        location_id="sector:farm",
    )


def _at(world, tick: int):
    world.current_tick = tick
    return world


@pytest.mark.asyncio
async def test_a_dusk_job_is_not_finished_while_the_sun_is_up(world) -> None:
    _at(world, 80)
    assert timestamp_for(80).is_daytime is True, "fixture assumption"

    assert _action(dusk=True).satisfied(world) is None


@pytest.mark.asyncio
async def test_a_dusk_job_stops_when_the_light_goes(world) -> None:
    _at(world, 0)
    assert timestamp_for(0).is_daytime is False, "fixture assumption"

    assert _action(dusk=True).satisfied(world) == "the light went"


@pytest.mark.asyncio
async def test_the_world_can_be_asked_whether_it_is_daytime(world) -> None:
    """The missing method itself, and that it agrees with the one clock.

    `world/core/engine.py:158` publishes `is_daytime` from
    `timestamp_for`. Two answers to "is the sun up" would mean a resident
    stopping work while the perception it is reading says it is noon.
    """
    for tick in (0, 40, 80, 120, 143):
        _at(world, tick)
        assert world.is_daytime() is timestamp_for(tick).is_daytime


@pytest.mark.asyncio
async def test_the_other_stop_conditions_still_answer(world) -> None:
    """The branch table this defect was hiding in."""
    _at(world, 10)

    done_units = _action(units=3)
    done_units.repetitions = 3
    assert done_units.satisfied(world) == "finished 3 of them"

    assert _action(ticks=5).satisfied(world) == "worked for 10 ticks"
    assert _action(ticks=50).satisfied(world) is None
