from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError, RecipeNotFoundError
from world.tools.locations import kitchen_location, mill_location, smithy_location
from world.tools.recipes import RECIPES
from world.tools.tests.conftest import spawn_agent


def test_recipe_table_covers_all_10_crops_5_metals_and_timber() -> None:
    from world.map.model import CROP_VARIETIES, FOREST_RESOURCE, ORE_METALS

    inputs = {r.input_item for r in RECIPES.values()}
    for crop in CROP_VARIETIES:
        assert crop in inputs, f"{crop} never appears as a recipe input"
    for metal in ORE_METALS:
        assert f"{metal}_ore" in inputs, f"{metal}_ore never appears as a recipe input"
    assert FOREST_RESOURCE in inputs


@pytest.mark.asyncio
async def test_craft_converts_wheat_to_flour(world, registry) -> None:
    await spawn_agent(world, "baker", mill_location())
    world.inventory.credit("baker", "wheat", 10)

    result = await registry.execute(
        world, "baker", "craft", {"recipe_id": "craft:wheat->flour", "batches": 2}
    )

    assert result.result == {"output_item": "flour", "output_qty": 2}
    assert world.inventory.quantity("baker", "wheat") == 8
    assert world.inventory.quantity("baker", "flour") == 2


@pytest.mark.asyncio
async def test_craft_wrong_location_is_gated(world, registry) -> None:
    await spawn_agent(world, "baker", "park")
    world.inventory.credit("baker", "wheat", 10)
    with pytest.raises(LocationGateError):
        await registry.execute(world, "baker", "craft", {"recipe_id": "craft:wheat->flour"})


@pytest.mark.asyncio
async def test_craft_unknown_recipe(world, registry) -> None:
    await spawn_agent(world, "baker", mill_location())
    with pytest.raises(RecipeNotFoundError):
        await registry.execute(world, "baker", "craft", {"recipe_id": "craft:does->not_exist"})


@pytest.mark.asyncio
async def test_craft_rejects_a_cook_recipe(world, registry) -> None:
    """`craft` and `cook` are strictly partitioned by ProductionTool - a
    cook-only recipe id must not be craftable even at the right arena."""
    await spawn_agent(world, "baker", kitchen_location())
    world.inventory.credit("baker", "flour", 10)
    with pytest.raises(RecipeNotFoundError):
        await registry.execute(world, "baker", "craft", {"recipe_id": "cook:flour->bread"})


@pytest.mark.asyncio
async def test_cook_converts_flour_to_bread(world, registry) -> None:
    await spawn_agent(world, "cook1", kitchen_location())
    world.inventory.credit("cook1", "flour", 4)

    result = await registry.execute(world, "cook1", "cook", {"recipe_id": "cook:flour->bread"})

    assert result.result == {"output_item": "bread", "output_qty": 1}
    assert world.inventory.quantity("cook1", "flour") == 3
    assert world.inventory.quantity("cook1", "bread") == 1


@pytest.mark.asyncio
async def test_metal_chain_ore_to_ingot_to_tool(world, registry) -> None:
    await spawn_agent(world, "smith", smithy_location())
    world.inventory.credit("smith", "iron_ore", 5)

    await registry.execute(world, "smith", "craft", {"recipe_id": "craft:iron_ore->iron_ingot"})
    assert world.inventory.quantity("smith", "iron_ingot") == 1

    await registry.execute(world, "smith", "craft", {"recipe_id": "craft:iron_ingot->iron_tool"})
    assert world.inventory.quantity("smith", "iron_tool") == 1
    assert world.inventory.quantity("smith", "iron_ingot") == 0


@pytest.mark.asyncio
async def test_insufficient_input_raises_and_produces_nothing(world, registry) -> None:
    await spawn_agent(world, "baker", mill_location())
    from world.tools.errors import InsufficientInventoryError

    with pytest.raises(InsufficientInventoryError):
        await registry.execute(world, "baker", "craft", {"recipe_id": "craft:wheat->flour"})
    assert world.inventory.quantity("baker", "flour") == 0
