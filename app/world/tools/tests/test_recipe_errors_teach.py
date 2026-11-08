"""A refused recipe id has to say what the valid ones are.

"no recipe 'potato_soup'" tells an agent its guess was wrong and gives it
nothing better to guess next, so the next turn spends itself on another
invention. Measured in one 19-tick run on the ten-resident cast:
wood_chair, timber_beam, processed_timber, potato_soup and potato - five
turns, five inventions, against a fixed list nobody had ever shown them.

Marta Thorne spent that run looking for flour she could not name.
"""

from __future__ import annotations

import pytest

from world.tools.errors import RecipeNotFoundError
from world.tools.primitives.production import require_recipe
from world.tools.recipes import RECIPES, ProductionTool


def test_the_refusal_lists_what_the_tool_makes() -> None:
    with pytest.raises(RecipeNotFoundError) as caught:
        require_recipe("potato_soup", ProductionTool.COOK)
    assert "cook:potato->baked_potato" in str(caught.value)


def test_it_lists_only_that_tools_recipes() -> None:
    """`cook` has no business advertising what the sawmill makes - the
    question being asked is what THIS tool accepts."""
    with pytest.raises(RecipeNotFoundError) as caught:
        require_recipe("potato_soup", ProductionTool.COOK)
    message = str(caught.value)
    craft_only = [r for r, spec in RECIPES.items() if spec.tool is ProductionTool.CRAFT]
    assert craft_only
    for recipe_id in craft_only:
        assert recipe_id not in message


def test_every_listed_recipe_is_one_the_tool_really_accepts() -> None:
    """The list has to be built from the registry, not typed out beside
    it, or it goes stale the first time a recipe is added."""
    with pytest.raises(RecipeNotFoundError) as caught:
        require_recipe("nonsense", ProductionTool.CRAFT)
    for recipe_id in caught.value.available:
        assert RECIPES[recipe_id].tool is ProductionTool.CRAFT


def test_a_real_recipe_still_resolves(tool_and_recipe=None) -> None:
    for recipe_id, spec in RECIPES.items():
        assert require_recipe(recipe_id, spec.tool) is spec


def test_a_recipe_from_the_wrong_tool_is_still_refused() -> None:
    """Asking `cook` for a craft recipe is a real mistake and stays one -
    it is now a mistake with the right answer attached."""
    craft_id = next(r for r, s in RECIPES.items() if s.tool is ProductionTool.CRAFT)
    with pytest.raises(RecipeNotFoundError):
        require_recipe(craft_id, ProductionTool.COOK)


def test_the_error_still_reads_correctly_with_no_list() -> None:
    """Raised bare elsewhere, it must not print a dangling clause."""
    assert str(RecipeNotFoundError("x")) == "no recipe 'x'"
