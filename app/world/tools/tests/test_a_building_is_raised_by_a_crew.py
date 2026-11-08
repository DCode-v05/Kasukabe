"""A building is raised by people, not by a clock running out.

`GROW-18`. `completion_tick = now + area * 6` was the whole of it. No
crew, no hands, no `keep_doing` - and `CONTINUABLE_TOOLS` excluded every
building verb, so there was no verb to keep doing. A licensed builder
could break ground and walk away, and the town handed them a finished
smithy on the deadline.

DESIGN section 2: "construction occupies the crew for time proportional
to size". The size half was implemented; the occupies-the-crew half was
not.

`work_site` is that half. It is continuable for the same reason `harvest`
and `craft` are - a builder who has to re-decide to keep building every
tick pays a model call per tick to do one job - and it is deliberately
NOT licence-gated: the builder needs the licence to open a site, the
hands carrying timber do not, and requiring it would mean GROW-10's
two-person yard could never put two people on a job.

Both gates stay, not either. The deadline alone let a builder walk away;
labour alone would let a crew of twelve raise a cathedral in an
afternoon.
"""
from __future__ import annotations

import pytest

from world.tools.errors import ActorRequirementError, LocationGateError
from world.tools.models import BuildingStatus, Plot
from world.tools.primitives.construction import (
    buildings_due_for_completion,
    complete_construction,
)
from world.tools.state import CONTINUABLE_TOOLS

from .conftest import move_agent
from .test_construction import license_builder, spawn_agent  # noqa: F401


async def _a_site(world, registry, agent_id: str = "builder"):
    await spawn_agent(world, agent_id, "plot-1")
    await license_builder(world, agent_id)
    world.plots["plot-1"] = Plot(
        plot_id="plot-1", owner_agent_id=agent_id, location_id="plot-1",
        footprint=frozenset({(0, 0)}), permitted=True,
    )
    world.inventory.credit(agent_id, "timber", 100)
    world.inventory.credit(agent_id, "iron_ore", 100)
    result = await registry.execute(
        world, agent_id, "build_structure",
        {"plot_id": "plot-1", "footprint": [[0, 0]], "purpose": "shed"},
    )
    return result.result["building_id"]


def test_the_building_verb_is_continuable() -> None:
    """The finding names this set explicitly. Without it there is no verb
    `keep_doing` can repeat, so building costs a decision every tick."""
    assert "work_site" in CONTINUABLE_TOOLS, (
        "GROW-18: no building verb is continuable, so a resident pays a "
        "model call for every tick of a job that takes dozens"
    )


@pytest.mark.asyncio
async def test_a_new_site_needs_the_work_the_deadline_implies(world, registry) -> None:
    """The labour is the same arithmetic as the countdown, so a crew that
    works every tick finishes exactly when the old clock would have."""
    building_id = await _a_site(world, registry)
    building = world.buildings[building_id]

    assert building.labour_ticks_required > 0
    assert building.labour_ticks_done == 0
    assert building.labour_ticks_required == (
        building.completion_tick - building.started_tick
    ), (
        "GROW-18: the work required and the deadline disagree, so a crew "
        "working flat out either finishes early or can never finish"
    )


@pytest.mark.asyncio
async def test_the_deadline_alone_no_longer_finishes_it(world, registry) -> None:
    """The finding. Before this, reaching the tick was the whole test."""
    building_id = await _a_site(world, registry)
    world.current_tick = world.buildings[building_id].completion_tick

    with pytest.raises(ActorRequirementError) as refused:
        await complete_construction(world, building_id)
    assert "work on site" in str(refused.value), (
        "GROW-18: the refusal does not say what is missing, so an agent "
        "reading it cannot tell what to do next"
    )


@pytest.mark.asyncio
async def test_a_site_nobody_worked_is_not_due(world, registry) -> None:
    """Not merely un-completable - not DUE. Otherwise the tick loop calls
    complete_construction on it every tick for the rest of the run and
    swallows the refusal each time."""
    building_id = await _a_site(world, registry)
    world.current_tick = world.buildings[building_id].completion_tick
    assert buildings_due_for_completion(world) == []


@pytest.mark.asyncio
async def test_working_the_site_finishes_it(world, registry) -> None:
    building_id = await _a_site(world, registry)
    while world.buildings[building_id].labour_remaining:
        await registry.execute(
            world, "builder", "work_site", {"building_id": building_id}
        )
    world.current_tick = world.buildings[building_id].completion_tick

    assert buildings_due_for_completion(world) == [building_id]
    event = await complete_construction(world, building_id)
    assert event.type == "world.building.completed"
    assert world.buildings[building_id].status is BuildingStatus.COMPLETE


@pytest.mark.asyncio
async def test_the_labour_alone_does_not_beat_the_clock(world, registry) -> None:
    """Both gates, not either. A crew of twelve must not raise a
    cathedral in an afternoon."""
    building_id = await _a_site(world, registry)
    while world.buildings[building_id].labour_remaining:
        await registry.execute(
            world, "builder", "work_site", {"building_id": building_id}
        )
    # Deliberately do NOT advance the clock.
    with pytest.raises(ActorRequirementError) as refused:
        await complete_construction(world, building_id)
    assert "not due until" in str(refused.value)


@pytest.mark.asyncio
async def test_a_tick_of_work_is_reported(world, registry) -> None:
    """The crew has to be able to see progress, or the only way to learn
    how far along a job is is to try to finish it."""
    building_id = await _a_site(world, registry)
    outcome = await registry.execute(
        world, "builder", "work_site", {"building_id": building_id}
    )
    assert outcome.result["labour_ticks_done"] == 1
    assert outcome.result["labour_remaining"] == (
        world.buildings[building_id].labour_ticks_required - 1
    )
    assert outcome.events[0].type == "world.building.worked"
    # UI-33 asks every world.building.* event to carry {rect, kind,
    # status}; this is the third such event.
    assert set(outcome.events[0].payload) >= {"rect", "kind", "status"}


@pytest.mark.asyncio
async def test_you_cannot_work_a_site_you_are_not_standing_on(world, registry) -> None:
    """The same rule `build_structure` follows (GROW-17). A crew that is
    not on site is not a crew."""
    building_id = await _a_site(world, registry)
    # The real event path, so LocationIndex updates the way it does in
    # production - reaching into state directly would test a shape
    # rather than the behaviour.
    await move_agent(world, "builder", "Hobbs Cafe")

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "builder", "work_site", {"building_id": building_id}
        )


@pytest.mark.asyncio
async def test_a_finished_site_refuses_more_work(world, registry) -> None:
    """Otherwise a crew could bank labour against a building that is
    already up, and the count would stop meaning anything."""
    building_id = await _a_site(world, registry)
    while world.buildings[building_id].labour_remaining:
        await registry.execute(
            world, "builder", "work_site", {"building_id": building_id}
        )
    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "builder", "work_site", {"building_id": building_id}
        )


@pytest.mark.asyncio
async def test_an_unlicensed_hand_may_still_carry_timber(world, registry) -> None:
    """Deliberate. The builder needs the licence to OPEN a site; the
    hands do not, or GROW-10's two-person yard could never put two people
    on one job."""
    building_id = await _a_site(world, registry)
    await spawn_agent(world, "hand", "plot-1")

    outcome = await registry.execute(
        world, "hand", "work_site", {"building_id": building_id}
    )
    assert outcome.result["labour_ticks_done"] == 1
