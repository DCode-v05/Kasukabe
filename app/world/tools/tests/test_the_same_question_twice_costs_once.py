"""Asking the same thing twice in one turn does not have a new answer.

`TRADE-04`. Run `deni-a6f9eb4-5h` made 1,224 tool calls that reached the
world. **199 of them — one in six — were an exact repeat of a call the
same resident had already made on the same tick**: same verb, same
parameters, same tick. `move` 64 times, `rest` 61, `look_around` 60.

A resident looked around, read the answer, and looked around again. Each
repeat was billed, each spent one of a tick's small call budget, and not
one could have returned anything new — because between two calls inside
a single turn, the only thing that can change the town is the resident
itself.

Codex's base instructions carry the same rule for the same reason:
*"Do not waste tokens by re-reading files after calling `apply_patch` on
them. The tool call will fail if it didn't work."* The town never said
the equivalent, so re-reading looked free and prudent.

A repeated READ now returns the answer it already gave, plus the
sentence the resident was missing, and is not charged. Repeated ACTS are
untouched — harvesting twice gets two harvests, and a resident that says
the same thing twice is repeating itself, which is real and worth
measuring rather than preventing.
"""
from __future__ import annotations

import pytest

from world.tools.repeated_calls import READ_ONLY_TOOLS
from world.tools.tests.conftest import spawn_agent

FARM = "Kasukabe Farmland"


@pytest.mark.asyncio
async def test_a_repeated_look_is_answered_and_not_charged(world, registry):
    """`describe_primitives`, not `look_around`: the latter is priced at zero,
    so it can show the fold but not the saving. This one costs 5."""
    await spawn_agent(world, "n01", FARM)

    before = world.ledger.sim_balance("n01")
    first = await registry.dispatch(world, "n01", "describe_primitives", {})
    one = before - world.ledger.sim_balance("n01")
    assert one > 0, "describe_primitives is free here; this test measures nothing"

    before = world.ledger.sim_balance("n01")
    again = await registry.dispatch(world, "n01", "describe_primitives", {})
    repeat = before - world.ledger.sim_balance("n01")

    assert repeat == 0, f"the same question cost {repeat} the second time"
    # Answered, not refused. The resident still gets what it asked for.
    assert again.result["repeat_of_this_turn"] is True
    assert "nothing has changed since" in again.result["note"]


@pytest.mark.asyncio
async def test_a_free_read_still_folds_and_says_so(world, registry):
    """`look_around` costs nothing, so the fold saves no sim - but the
    sentence is the point as much as the money: a resident that is told
    the answer has not changed stops asking."""
    await spawn_agent(world, "n01", FARM)
    first = await registry.dispatch(world, "n01", "look_around", {})
    again = await registry.dispatch(world, "n01", "look_around", {})

    assert again.result["location_id"] == first.result["location_id"]
    assert again.result["repeat_of_this_turn"] is True


@pytest.mark.asyncio
async def test_the_same_question_on_a_later_tick_is_a_real_question(world, registry):
    """The world moves between turns. Only within one is the answer fixed."""
    await spawn_agent(world, "n01", FARM)
    await registry.dispatch(world, "n01", "describe_primitives", {})

    world.current_tick += 1
    before = world.ledger.sim_balance("n01")
    outcome = await registry.dispatch(world, "n01", "describe_primitives", {})

    assert before - world.ledger.sim_balance("n01") > 0
    assert "repeat_of_this_turn" not in outcome.result


@pytest.mark.asyncio
async def test_a_different_question_is_charged(world, registry):
    await spawn_agent(world, "n01", FARM)
    await spawn_agent(world, "n02", FARM)
    world.agent_names["n02"] = "Somebody"

    await registry.dispatch(world, "n01", "describe_person", {"agent_id": "n02"})
    before = world.ledger.sim_balance("n01")
    await registry.dispatch(world, "n01", "describe_person", {"agent_id": "n01"})

    assert before - world.ledger.sim_balance("n01") > 0, (
        "asking about a different person folded as a repeat"
    )


@pytest.mark.asyncio
async def test_another_resident_asking_the_same_thing_pays(world, registry):
    """The fold is per-resident. n02 has not asked anything yet."""
    await spawn_agent(world, "n01", FARM)
    await spawn_agent(world, "n02", FARM)

    await registry.dispatch(world, "n01", "describe_primitives", {})
    before = world.ledger.sim_balance("n02")
    await registry.dispatch(world, "n02", "describe_primitives", {})

    assert before - world.ledger.sim_balance("n02") > 0


@pytest.mark.asyncio
async def test_a_repeated_act_is_not_folded(world, registry):
    """Harvesting twice gets two harvests. Only reads are folded, and the
    list is explicit rather than inferred - a tool that quietly acquires
    a lock or wakes somebody would pass any derived test."""
    await spawn_agent(world, "n01", FARM)

    await registry.dispatch(world, "n01", "harvest", {"crop": "wheat"})
    held = world.inventory.quantity("n01", "wheat")
    before = world.ledger.sim_balance("n01")
    await registry.dispatch(world, "n01", "harvest", {"crop": "wheat"})

    assert world.inventory.quantity("n01", "wheat") > held, (
        "a second harvest was folded away as a repeat"
    )
    assert before - world.ledger.sim_balance("n01") > 0


def test_only_reporting_tools_are_on_the_list():
    """The list is the whole safety of this. Anything that changes the
    world must not be on it."""
    for name in ("harvest", "mine", "say", "move", "buy", "sell", "give",
                 "transfer", "post_price", "stock_shelf", "eat", "rest",
                 "craft", "cook", "set_goal", "set_intention"):
        assert name not in READ_ONLY_TOOLS, f"{name} changes the world"


def test_every_name_on_the_list_is_a_tool_that_exists():
    """A rule keyed to a name nothing registers is a rule that silently
    never applies.

    The first draft of `READ_ONLY_TOOLS` carried seven such names -
    `check_wallet`, `check_inventory`, `check_prices`,
    `authoring_context` and the three memory verbs. The memory verbs are
    real but live engine-side and never pass through this registry; the
    other four do not exist at all. `ALWAYS_IN_FULL` in registry.py named
    two of the same ghosts plus `sleep`.

    This is the EVAL-01 failure - a reader keyed to names nothing writes -
    and it is worth a sweep rather than a memory.
    """
    import asyncio

    from world.tools.bootstrap import build_world
    from world.tools.registry import ALWAYS_IN_FULL

    async def build():
        return await build_world(genesis_tick=0, seed=1)

    town, registry = asyncio.run(build())
    for name in sorted(READ_ONLY_TOOLS):
        assert registry.get(name) is not None, (
            f"READ_ONLY_TOOLS names {name!r}, which no tool registers - "
            "the fold can never fire for it"
        )
    for name in sorted(ALWAYS_IN_FULL):
        assert registry.get(name) is not None, (
            f"ALWAYS_IN_FULL names {name!r}, which no tool registers - "
            "it promises a verb the town does not have"
        )


# ── TRADE-06, second attempt: priced, not folded ─────────────────────


@pytest.mark.asyncio
async def test_a_journey_of_zero_tiles_is_free(world, registry):
    """`move` was the most repeated verb in run `deni-a6f9eb4-5h` — 64
    exact repeats — and a resident asking to go where it already stands
    was charged, told `walked: true`, and given no reason to stop.

    The FIRST attempt at this folded the call before the handler, which
    dropped `agent.moved` and left a result claiming "nothing was
    charged" while dispatch had already charged. This one only answers a
    pricing question: the handler still runs, the event still publishes.
    """
    await spawn_agent(world, "n01", "Johnson Park")
    here = world.location_of("n01")

    before = world.ledger.sim_balance("n01")
    outcome = await registry.execute(world, "n01", "move", {"destination_location_id": here})

    assert world.ledger.sim_balance("n01") == before, "a walk of zero tiles was billed"
    # And the wire survives - this is what the first attempt broke.
    assert outcome.events, "the arrival published nothing; the map cannot see it"
    assert any(e.type == "agent.moved" for e in outcome.events)


@pytest.mark.asyncio
async def test_a_real_walk_is_still_paid_for(world, registry):
    """The other direction. Free movement would make distance free, which
    is the ECON-18 defect with the sign flipped."""
    await spawn_agent(world, "n01", "Johnson Park")

    before = world.ledger.sim_balance("n01")
    await registry.execute(
        world, "n01", "move", {"destination_location_id": "Kasukabe Farmland"}
    )

    assert world.ledger.sim_balance("n01") < before, "walking across town became free"


@pytest.mark.asyncio
async def test_resting_twice_is_still_two_units_of_work(world, registry):
    """`rest` is NOT on the free list and must not go on it.

    It looks idempotent and is not: `keep_doing` repeats it through this
    same dispatch precisely so each repetition earns proficiency and is
    charged. The first attempt at TRADE-06 folded it and silently
    disabled that whole feature.
    """
    await spawn_agent(world, "n01", "Johnson Park")

    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "rest", {})
    await registry.execute(world, "n01", "rest", {})

    assert world.ledger.sim_balance("n01") <= before - 2, (
        "two rests were billed as one - keep_doing's proficiency path is broken"
    )
