"""TOOL-04/13/14: three ways the town withheld what it already knew.

**TOOL-04.** The standing brief reduced a whole inventory to one number -
"food units" - and `look_around` returned place, people, conversations and
prices but never holdings. No `check_inventory` tool existed. So the model
read the brief's own word back at the world:

    eat: 'food' is not food                      x4
    you hold N X, not M  (give / trade / eat)   x13

**TOOL-13.** The engine shows every tool namespaced, and a resident names
back what it was shown. Both of run 23's `keep_doing` calls died on
"'world_mine' is not something you can keep doing", and `set_intention`
refused `world_cook` twice. `skillify` has folded this since Marta lost a
six-step workflow the same way; the fold never reached the other two verbs
that take a tool name.

**TOOL-14.** `craft`'s description was "Convert raw material into a
processed good via a recipe" and named not one of the twenty-eight. 18 of
23 calls refused, 13 of them on ids invented from that sentence.
"""

from __future__ import annotations

import pytest

from world.tools.inventory import holdings_line
from world.tools.recipes import RECIPES

from .conftest import spawn_agent

HERE = "Kasukabe Town Square"


@pytest.mark.asyncio
async def test_holdings_are_ranked_biggest_pile_first(world):
    await spawn_agent(world, "n01", HERE)
    world.inventory.credit("n01", "wheat", 9)
    world.inventory.credit("n01", "bread", 2)
    world.inventory.credit("n01", "timber", 40)

    held = holdings_line(world, "n01")

    assert list(held.items())[0] == ("timber", 40)
    assert held == {"timber": 40, "wheat": 9, "bread": 2}


@pytest.mark.asyncio
async def test_an_empty_hand_is_empty_not_noise(world):
    await spawn_agent(world, "n01", HERE)
    assert holdings_line(world, "n01") == {}


@pytest.mark.asyncio
async def test_a_long_list_is_capped_and_says_so(world):
    """A truncated list that looks complete is worse than a short one."""
    await spawn_agent(world, "n01", HERE)
    for i in range(12):
        world.inventory.credit("n01", f"item_{i:02d}", i + 1)

    held = holdings_line(world, "n01", limit=8)

    assert len(held) == 9
    assert held["…and more kinds"] == 4


@pytest.mark.asyncio
async def test_look_around_says_what_you_are_carrying(world, registry):
    """The two facts a decision to buy is made from, in one breath."""
    await spawn_agent(world, "n01", HERE)
    world.inventory.credit("n01", "wheat", 5)

    outcome = await registry.execute(world, "n01", "look_around", {})

    assert outcome.result["you_are_holding"] == {"wheat": 5}


@pytest.mark.asyncio
async def test_look_around_is_honest_about_holding_nothing(world, registry):
    await spawn_agent(world, "n01", HERE)
    outcome = await registry.execute(world, "n01", "look_around", {})
    assert "nothing" in str(outcome.result["you_are_holding"])


def test_the_brief_renders_the_holdings_line():
    from world.retrieval_depth import format_standing_brief

    rendered = format_standing_brief(
        {
            "name": "Marta Thorne",
            "occupation": "baker",
            "location_id": HERE,
            "food_units": 0,
            "holding": {"flour": 20, "timber": 3},
            "sim_balance": 400,
        }
    )

    assert "You hold: flour 20, timber 3." in rendered


@pytest.mark.asyncio
async def test_keep_doing_accepts_the_name_the_engine_showed(world, registry):
    """Both of run 23's `keep_doing` calls died on exactly this."""
    await spawn_agent(world, "n01", "Kasukabe Ore Outcrop")

    outcome = await registry.execute(
        world,
        "n01",
        "keep_doing",
        {"tool": "world_mine", "params": {"metal": "iron", "quantity": 1}, "until": {"ticks": 5}},
    )

    assert outcome.result["tool"] == "mine"
    assert world.continuing["n01"].tool == "mine"


@pytest.mark.asyncio
async def test_keep_doing_accepts_the_colon_spelling_too(world, registry):
    await spawn_agent(world, "n01", "Kasukabe Ore Outcrop")

    await registry.execute(
        world,
        "n01",
        "keep_doing",
        {"tool": "world:mine", "params": {"metal": "iron", "quantity": 1}, "until": {"ticks": 5}},
    )
    assert world.continuing["n01"].tool == "mine"


@pytest.mark.asyncio
async def test_keep_doing_still_refuses_a_verb_you_cannot_repeat(world, registry):
    """The fold must not turn every name into a continuable one."""
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", HERE)

    with pytest.raises(ToolValidationError):
        await registry.execute(
            world, "n01", "keep_doing", {"tool": "world_say", "params": {}, "until": {"ticks": 5}}
        )


@pytest.mark.asyncio
async def test_set_intention_accepts_the_prefixed_done_when_tool(world, registry):
    await spawn_agent(world, "n01", HERE)

    await registry.execute(
        world,
        "n01",
        "set_intention",
        {"text": "bake bread before dusk", "done_when_tool": "world_cook"},
    )

    assert world.intentions["n01"].done_when_tool == "cook"


@pytest.mark.asyncio
async def test_set_intention_still_refuses_a_verb_that_does_not_exist(world, registry):
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", HERE)

    with pytest.raises(ToolValidationError):
        await registry.execute(
            world,
            "n01",
            "set_intention",
            {"text": "do the impossible", "done_when_tool": "world_teleport"},
        )


def test_craft_and_cook_name_their_recipes():
    from world.tools.primitives.production import build_specs

    specs = {spec.name: spec for spec in build_specs()}

    craft_ids = {r.recipe_id for r in RECIPES.values() if r.recipe_id.startswith("craft:")}
    cook_ids = {r.recipe_id for r in RECIPES.values() if r.recipe_id.startswith("cook:")}

    for recipe_id in craft_ids:
        assert recipe_id in specs["craft"].description, recipe_id
    for recipe_id in cook_ids:
        assert recipe_id in specs["cook"].description, recipe_id

    assert "craft:iron_ore->iron_ingot" not in specs["cook"].description


def test_the_recipe_menu_says_where_each_one_works():
    from world.tools.primitives.production import build_specs

    craft = {spec.name: spec for spec in build_specs()}["craft"].description
    assert "at the mill:" in craft
    assert "at the smithy:" in craft
