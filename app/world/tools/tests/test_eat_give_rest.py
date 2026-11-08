"""§2.4-§2.6 — a resident must be able to eat, to give, and to stop.

Run 15 held 48 tools and none of them consumed food. `transfer` moves
money only (to_agent_id, amount_sim); `sell` requires a business_id, so
goods could only ever pass to a shop. There was no way to eat and no way
to hand anything to anyone.

The residents hit all three walls in dialogue:

    Marta Thorne:  "I hold 5 loaves I haven't given anyone."
    Corwin Thorne: "If taking it's possible, I will - I've got nothing in
                    my belly and household upkeep won't care whether I
                    found a meal or not."
    Marta Thorne:  "I need - sit down. Please. Sit down with me. I need to
                    not be moving."

Corwin starved standing beside a baker holding five loaves. Neither of
them was able to do anything about it, and "if taking it's possible" is
an agent correctly inferring the affordance might not exist.

The survival loop was harvest -> cook -> nothing.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


@pytest_asyncio.fixture
async def town():
    return await build_world(genesis_tick=0, seed=1)


async def _spawn(world, agent_id: str, where: str) -> None:
    from world.tools.tests.conftest import spawn_agent  # type: ignore

    await spawn_agent(world, agent_id, where)


@pytest.mark.parametrize("tool", ["eat", "give", "rest"])
def test_the_tool_is_registered(town, tool: str) -> None:
    _world, registry = town
    assert registry.get(tool) is not None, (
        f"there is no `{tool}` tool - the town cannot "
        + {"eat": "consume the food it grows",
           "give": "hand anything to anyone",
           "rest": "stop moving"}[tool]
    )


def test_eat_is_discoverable_alongside_the_food_tools(town) -> None:
    """A tool nobody is told about is not shipped. `cook` and `harvest`
    are public; `eat` must be too, or the loop still has no visible end."""
    _world, registry = town
    from world.tools.registry import ToolNamespace

    assert registry.get("eat").namespace == ToolNamespace.PUBLIC
    assert registry.get("give").namespace == ToolNamespace.PUBLIC


@pytest.mark.asyncio
async def test_eating_consumes_exactly_what_was_eaten(town) -> None:
    world, registry = town
    await _spawn(world, "a1", "Hobbs Cafe")
    world.inventory.credit("a1", "bread", 3)

    await registry.execute(world, "a1", "eat", {"item": "bread", "quantity": 2})

    assert world.inventory.balances["a1"]["bread"] == 1


@pytest.mark.asyncio
async def test_eating_says_you_ate_it(town) -> None:
    """The result has to tell the agent it worked, in terms it can act on
    next turn - a silent success is indistinguishable from a refusal."""
    world, registry = town
    await _spawn(world, "a1", "Hobbs Cafe")
    world.inventory.credit("a1", "bread", 1)

    outcome = await registry.execute(world, "a1", "eat", {"item": "bread", "quantity": 1})

    assert "bread" in str(outcome.result)
    assert any(e.type == "agent.ate" for e in outcome.events), (
        "eating must publish an event, or nothing downstream can clear hunger"
    )


@pytest.mark.asyncio
async def test_eating_what_you_do_not_hold_is_refused(town) -> None:
    world, registry = town
    await _spawn(world, "a1", "Hobbs Cafe")
    with pytest.raises(Exception):
        await registry.execute(world, "a1", "eat", {"item": "bread", "quantity": 1})


@pytest.mark.asyncio
async def test_you_cannot_eat_timber(town) -> None:
    """The town already agrees on what counts as food. Eating a plank must
    be refused, and the refusal must name what IS edible - a bare "no" is
    the dead end that produced a 5% recovery rate."""
    world, registry = town
    await _spawn(world, "a1", "Hobbs Cafe")
    world.inventory.credit("a1", "timber", 5)

    with pytest.raises(Exception) as caught:
        await registry.execute(world, "a1", "eat", {"item": "timber", "quantity": 1})
    assert "bread" in str(caught.value) or "wheat" in str(caught.value)


@pytest.mark.asyncio
async def test_giving_moves_goods_and_conserves_them(town) -> None:
    """What one resident loses, another gains, exactly. This is the case
    Marta and Corwin could not perform."""
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    await _spawn(world, "corwin", "Hobbs Cafe")
    world.inventory.credit("marta", "bread", 5)

    await registry.execute(
        world, "marta", "give",
        {"to_agent_id": "corwin", "item": "bread", "quantity": 2},
    )

    assert world.inventory.balances["marta"]["bread"] == 3
    assert world.inventory.balances["corwin"]["bread"] == 2


@pytest.mark.asyncio
async def test_giving_to_somebody_elsewhere_is_refused_usefully(town) -> None:
    """You cannot hand bread across town. The refusal must say so in terms
    the agent can act on, the way `say` reports who was not there."""
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    await _spawn(world, "corwin", "Kasukabe Forest")
    world.inventory.credit("marta", "bread", 1)

    with pytest.raises(Exception) as caught:
        await registry.execute(
            world, "marta", "give",
            {"to_agent_id": "corwin", "item": "bread", "quantity": 1},
        )
    assert "corwin" in str(caught.value).lower() or "place" in str(caught.value).lower()


@pytest.mark.asyncio
async def test_giving_what_you_do_not_hold_is_refused(town) -> None:
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    await _spawn(world, "corwin", "Hobbs Cafe")
    with pytest.raises(Exception):
        await registry.execute(
            world, "marta", "give",
            {"to_agent_id": "corwin", "item": "bread", "quantity": 1},
        )


@pytest.mark.asyncio
async def test_giving_to_yourself_does_not_duplicate_goods(town) -> None:
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    world.inventory.credit("marta", "bread", 2)

    with pytest.raises(Exception):
        await registry.execute(
            world, "marta", "give",
            {"to_agent_id": "marta", "item": "bread", "quantity": 1},
        )
    assert world.inventory.balances["marta"]["bread"] == 2


@pytest.mark.asyncio
async def test_giving_is_witnessed(town) -> None:
    """A gift is a social act. It must publish an event, so the room can
    know it happened and the record can show who fed whom."""
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    await _spawn(world, "corwin", "Hobbs Cafe")
    world.inventory.credit("marta", "bread", 1)

    outcome = await registry.execute(
        world, "marta", "give",
        {"to_agent_id": "corwin", "item": "bread", "quantity": 1},
    )
    assert any(e.type == "agent.gave" for e in outcome.events)


@pytest.mark.asyncio
async def test_resting_is_something_a_resident_can_actually_do(town) -> None:
    """Marta asked Wren to sit down with her and no such affordance
    existed. Resting must succeed, cost nothing to speak of, and leave the
    resident where they are."""
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")

    before = world.location_of("marta")
    outcome = await registry.execute(world, "marta", "rest", {})

    assert world.location_of("marta") == before
    assert outcome.result is not None


@pytest.mark.asyncio
async def test_resting_stops_a_walk_when_asked(town) -> None:
    """"I need to not be moving" has to mean it - when it is what was meant.

    TOOL-05(d) split this in two. `rest` used to end EVERY journey while
    its description said "Stay where you are for now", so a resident
    pausing mid-walk silently threw away ticks of walking it had already
    paid for: twelve of run 23's thirty-six rests were that. Giving up a
    journey is still one parameter away and is now a decision.
    """
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    await registry.execute(
        world, "marta", "move", {"destination_location_id": "Kasukabe Farmland"}
    )
    assert world.movement.has_active_journey("marta")

    outcome = await registry.execute(world, "marta", "rest", {"stop_walking": True})

    assert not world.movement.has_active_journey("marta"), (
        "stop_walking must end the journey - otherwise the drift continues"
    )
    assert outcome.result["stopped_walking"] is True
    assert outcome.result["still_walking"] is False


@pytest.mark.asyncio
async def test_resting_does_not_throw_away_a_walk_by_default(town) -> None:
    """TOOL-05(d), the half that was the defect."""
    world, registry = town
    await _spawn(world, "marta", "Hobbs Cafe")
    await registry.execute(
        world, "marta", "move", {"destination_location_id": "Kasukabe Farmland"}
    )
    assert world.movement.has_active_journey("marta")

    outcome = await registry.execute(world, "marta", "rest", {})

    assert world.movement.has_active_journey("marta"), (
        "a resident paused for one turn and lost the journey it was on"
    )
    assert outcome.result["stopped_walking"] is False
    assert outcome.result["still_walking"] is True
    assert "still walking to" in outcome.result["note"]
