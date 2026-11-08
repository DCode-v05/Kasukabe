"""An invented tool may answer a question, not just recite what it did.

The gap this closes, in the operator's words: *see world is a tool; the
agent uses toolify to make it better by adding layers, so that the
resultant tool will allow it to find and search for food - the new tool
becomes search_food wrapped around see world.*

That tool was unwritable. A composite's result was the account of the run
- `steps_executed`, `calls_made`, `last_step_result` - so `search_food`
could look around and then hand back a log with the whole place in it,
and the resident had to read the log to find the food. A tool that cannot
SELECT cannot be built on: every layer above it has to re-read everything
below it, which is why the town's tools never became a library.

`returns` is the projection, and it is data like everything else here: a
reference (`@bound.field`, `$param`) or one closed selector - take from
this list, keep the entries matching this comparison, give me all of them
or the first or how many there were. No expressions, no new physics, and
every comparison is the `when` vocabulary already in `StepCondition`.
"""

from __future__ import annotations

import pytest

from world.tools.models import Business
from world.tools.tests.conftest import spawn_agent

WHERE = "Kasukabe Farmland"


async def _a_shop_selling_bread(world, owner: str) -> None:
    world.businesses["b_shop"] = Business(
        business_id="b_shop", name="Kasukabe Grocery", business_type="grocery",
        owner_agent_id=owner, location_id=WHERE,
    )
    await world.ledger.register_business("b_shop", owner, fee_sim=1, tick=world.tick())
    await world.ledger.mint_sim("b_shop", 2_000, tick=world.tick())


async def _toolify(registry, world, agent, name, steps, returns=None, schema=None):
    return await registry.dispatch(world, agent, "toolify", {
        "name": name,
        "description": f"{name}, for the test",
        "param_schema": schema or {},
        "steps": steps,
        "returns": returns or {},
    })


@pytest.mark.asyncio
async def test_a_tool_can_search_for_food_and_answer_with_it(world, registry):
    """`search_food` wrapped around looking: the operator's own example."""
    await spawn_agent(world, "n01", WHERE)
    await _a_shop_selling_bread(world, "n01")
    await registry.dispatch(world, "n01", "post_price", {
        "business_id": "b_shop", "item": "bread", "ask_sim": 12,
    })

    await _toolify(
        registry, world, "n01", "search_food",
        [{"tool_name": "look_around", "params": {}, "bind": "here"}],
        returns={"food": {
            "from": "@here.prices_here",
            "where": {"op": "contains", "value": "bread"},
            "take": "all",
        }},
    )
    outcome = await registry.dispatch(world, "n01", "search_food", {})

    food = outcome.result["answer"]["food"]
    assert food and all("bread" in row for row in food), outcome.result
    assert not any("bread" not in row for row in food)


@pytest.mark.asyncio
async def test_an_answer_may_simply_name_what_a_step_found(world, registry):
    await spawn_agent(world, "n01", WHERE)
    await _toolify(
        registry, world, "n01", "where_am_i",
        [{"tool_name": "look_around", "params": {}, "bind": "here"}],
        returns={"place": "@here.location_id"},
    )

    outcome = await registry.dispatch(world, "n01", "where_am_i", {})

    assert outcome.result["answer"]["place"] == WHERE


@pytest.mark.asyncio
async def test_first_and_count_are_the_other_two_answers(world, registry):
    await spawn_agent(world, "n01", WHERE)
    await spawn_agent(world, "n02", WHERE)
    await spawn_agent(world, "n03", WHERE)
    await _toolify(
        registry, world, "n01", "who_is_here",
        [{"tool_name": "look_around", "params": {}, "bind": "here"}],
        returns={
            "nearest": {"from": "@here.people_here", "take": "first"},
            "how_many": {"from": "@here.people_here", "take": "count"},
        },
    )

    answer = (await registry.dispatch(world, "n01", "who_is_here", {})).result["answer"]

    assert answer["how_many"] == 2
    assert answer["nearest"] is not None


@pytest.mark.asyncio
async def test_a_tower_answers_with_what_the_tool_below_it_found(world, registry):
    """The layered version: the outer tool reads the inner tool's answer,
    which is what makes a library of tools worth having."""
    await spawn_agent(world, "n01", WHERE)
    await _toolify(
        registry, world, "n01", "see_world",
        [{"tool_name": "look_around", "params": {}, "bind": "here"}],
        returns={"place": "@here.location_id"},
    )
    await _toolify(
        registry, world, "n01", "name_my_place",
        [{"tool_name": "see_world", "params": {}, "bind": "seen"}],
        returns={"place": "@seen.answer"},
    )

    answer = (await registry.dispatch(world, "n01", "name_my_place", {})).result["answer"]

    assert answer["place"] == {"place": WHERE}


@pytest.mark.asyncio
async def test_an_answer_that_reads_nothing_is_refused_at_filing(world, registry):
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", WHERE)
    with pytest.raises(ToolValidationError) as raised:
        await _toolify(
            registry, world, "n01", "hopeful",
            [{"tool_name": "look_around", "params": {}}],
            returns={"food": "@here.prices_here"},
        )
    message = str(raised.value)
    assert "here" in message and "bind" in message


@pytest.mark.asyncio
async def test_an_answer_cannot_read_a_step_that_has_not_run_yet(world, registry):
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", WHERE)
    with pytest.raises(ToolValidationError):
        await _toolify(
            registry, world, "n01", "backwards",
            [
                {"tool_name": "rest", "params": {}, "when": {"left": "@later", "op": "truthy"}},
                {"tool_name": "look_around", "params": {}, "bind": "later"},
            ],
            returns={"x": "@later"},
        )


@pytest.mark.asyncio
async def test_a_selector_the_town_does_not_have_is_refused(world, registry):
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", WHERE)
    with pytest.raises(ToolValidationError) as raised:
        await _toolify(
            registry, world, "n01", "inventive",
            [{"tool_name": "look_around", "params": {}, "bind": "here"}],
            returns={"food": {"from": "@here.prices_here", "where": {"op": "smells_like", "value": "bread"}}},
        )
    assert "smells_like" in str(raised.value)

    with pytest.raises(ToolValidationError) as raised:
        await _toolify(
            registry, world, "n01", "inventive2",
            [{"tool_name": "look_around", "params": {}, "bind": "here"}],
            returns={"food": {"from": "@here.prices_here", "take": "some"}},
        )
    assert "some" in str(raised.value)


@pytest.mark.asyncio
async def test_one_call_run_now_can_answer_too(world, registry):
    """`do_in_order` is the same program without the filing, so it answers
    the same way - a resident can search once without keeping a tool."""
    await spawn_agent(world, "n01", WHERE)

    outcome = await registry.dispatch(world, "n01", "do_in_order", {
        "steps": [{"tool_name": "look_around", "params": {}, "bind": "here"}],
        "returns": {"place": "@here.location_id"},
    })

    assert outcome.result["answer"]["place"] == WHERE
