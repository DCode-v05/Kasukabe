from __future__ import annotations

from world.tools.recipes import RECIPES, ProductionTool, recipes_for_tool


def test_every_recipe_id_is_unique() -> None:
    ids = [r.recipe_id for r in RECIPES.values()]
    assert len(ids) == len(set(ids))


def test_craft_and_cook_partition_is_disjoint() -> None:
    craft_ids = {r.recipe_id for r in recipes_for_tool(ProductionTool.CRAFT)}
    cook_ids = {r.recipe_id for r in recipes_for_tool(ProductionTool.COOK)}
    assert craft_ids.isdisjoint(cook_ids)
    assert craft_ids | cook_ids == set(RECIPES.keys())


def test_every_recipe_has_a_required_location() -> None:
    for recipe in RECIPES.values():
        assert recipe.required_location


def test_cotton_is_the_two_stage_textile_chain() -> None:
    assert "craft:cotton->cotton_thread" in RECIPES
    assert "craft:cotton_thread->cloth" in RECIPES
    assert RECIPES["craft:cotton_thread->cloth"].output_item == "cloth"


def test_timber_chain_feeds_construction_and_furniture() -> None:
    assert RECIPES["craft:timber->planks"].output_item == "planks"
    assert RECIPES["craft:planks->furniture"].input_item == "planks"
