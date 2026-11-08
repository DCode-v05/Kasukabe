"""The craft/cook recipe table (DESIGN.md section 2: "craft (recipe-based
conversions: e.g. grain->flour->bread)"; the task spec for this module:
"recipe table: raw->goods chains for 10 crops, 5 metals, timber").

One Recipe is one input->output conversion, tagged with which primitive
tool executes it (`craft` or `cook`) and which production arena it
requires (locations.py). `craft` and `cook` are both thin dispatchers
over this same table (see primitives/production.py) rather than two
independent implementations, so the physics of "consume inputs, produce
output, at the right place" lives in exactly one place.

Every crop/metal name is imported from world.map.model (CROP_VARIETIES,
ORE_METALS, FOREST_RESOURCE) rather than redeclared here - that module
already asserts there are exactly 10 crops and 5 metals, and it is the
map module's job to say what a farmland strip or ore outcrop actually
grows, not this one's. Item quantities are all 1-in/1-out for now
(DESIGN.md does not pin conversion ratios); a game-balance pass changing
that only ever touches this file.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from world.map.model import CROP_VARIETIES, FOREST_RESOURCE, ORE_METALS
from world.tools.locations import (
    carpentry_location,
    kitchen_location,
    loom_house_location,
    mill_location,
    sawmill_location,
    smithy_location,
)


class ProductionTool(str, Enum):
    CRAFT = "craft"
    COOK = "cook"


class Recipe(BaseModel):
    """One input->output conversion. Frozen: recipes are fixed game data,
    never mutated in place - a game-balance change replaces the RECIPES
    table entry wholesale.
    """

    model_config = {"frozen": True}

    recipe_id: str
    tool: ProductionTool
    input_item: str
    input_qty: int = 1
    output_item: str
    output_qty: int = 1
    required_location: str


def crop_recipes() -> list[Recipe]:
    """Chains for the 10 crop varieties. Six feed a craft stage before
    they are cook-able (grain/fiber crops that need milling); the
    remaining four (potato, tomato, carrot, cabbage) are fresh produce
    that goes straight from farmland to the kitchen, matching how real
    root/garden vegetables need no milling step. Cotton is the one
    two-stage craft chain (raw -> thread -> cloth) and, being a textile,
    is never cooked at all.
    """
    recipes: list[Recipe] = []

    milled = {
        "wheat": "flour",
        "rice": "milled_rice",
        "corn": "corn_meal",
        "barley": "malt",
        "soybean": "tofu",
    }
    for crop, product in milled.items():
        assert crop in CROP_VARIETIES
        recipes.append(
            Recipe(
                recipe_id=f"craft:{crop}->{product}",
                tool=ProductionTool.CRAFT,
                input_item=crop,
                output_item=product,
                required_location=mill_location(),
            )
        )

    assert "cotton" in CROP_VARIETIES
    recipes.append(
        Recipe(
            recipe_id="craft:cotton->cotton_thread",
            tool=ProductionTool.CRAFT,
            input_item="cotton",
            output_item="cotton_thread",
            required_location=mill_location(),
        )
    )
    recipes.append(
        Recipe(
            recipe_id="craft:cotton_thread->cloth",
            tool=ProductionTool.CRAFT,
            input_item="cotton_thread",
            output_item="cloth",
            required_location=loom_house_location(),
        )
    )

    cooked_from_flour_stage = {
        "flour": "bread",
        "milled_rice": "cooked_rice",
        "corn_meal": "cornbread",
        "malt": "ale",
    }
    for milled_good, dish in cooked_from_flour_stage.items():
        recipes.append(
            Recipe(
                recipe_id=f"cook:{milled_good}->{dish}",
                tool=ProductionTool.COOK,
                input_item=milled_good,
                output_item=dish,
                required_location=kitchen_location(),
            )
        )

    fresh_produce = {
        "potato": "baked_potato",
        "tomato": "tomato_soup",
        "carrot": "carrot_soup",
        "cabbage": "cabbage_stew",
    }
    for crop, dish in fresh_produce.items():
        assert crop in CROP_VARIETIES
        recipes.append(
            Recipe(
                recipe_id=f"cook:{crop}->{dish}",
                tool=ProductionTool.COOK,
                input_item=crop,
                output_item=dish,
                required_location=kitchen_location(),
            )
        )

    return recipes


def metal_recipes() -> list[Recipe]:
    """Chains for the 5 ore metals: ore -> ingot -> a finished good, all
    at the smithy. Finished-good names are metal-appropriate (a wire for
    conductive copper, jewelry for the precious two) rather than one
    generic "<metal>_tool" for all five, so the recipe table itself
    documents what each metal is actually for.
    """
    recipes: list[Recipe] = []
    finished_good = {
        "iron": "iron_tool",
        "copper": "copper_wire",
        "tin": "tin_ware",
        "silver": "silver_jewelry",
        "gold": "gold_jewelry",
    }
    for metal in ORE_METALS:
        ore = f"{metal}_ore"
        ingot = f"{metal}_ingot"
        recipes.append(
            Recipe(
                recipe_id=f"craft:{ore}->{ingot}",
                tool=ProductionTool.CRAFT,
                input_item=ore,
                output_item=ingot,
                required_location=smithy_location(),
            )
        )
        recipes.append(
            Recipe(
                recipe_id=f"craft:{ingot}->{finished_good[metal]}",
                tool=ProductionTool.CRAFT,
                input_item=ingot,
                output_item=finished_good[metal],
                required_location=smithy_location(),
            )
        )
    return recipes


def timber_recipes() -> list[Recipe]:
    """DESIGN.md section 1: "forest (timber, common)". Planks are also the
    raw material build_structure's bill-of-materials consumes (see
    primitives/construction.py) - furniture is the separate finished-good
    chain a carpenter sells.
    """
    return [
        Recipe(
            recipe_id=f"craft:{FOREST_RESOURCE}->planks",
            tool=ProductionTool.CRAFT,
            input_item=FOREST_RESOURCE,
            output_item="planks",
            required_location=sawmill_location(),
        ),
        Recipe(
            recipe_id="craft:planks->furniture",
            tool=ProductionTool.CRAFT,
            input_item="planks",
            output_item="furniture",
            required_location=carpentry_location(),
        ),
    ]


def build_recipe_table() -> dict[str, Recipe]:
    all_recipes = crop_recipes() + metal_recipes() + timber_recipes()
    table = {recipe.recipe_id: recipe for recipe in all_recipes}
    assert len(table) == len(all_recipes), "duplicate recipe_id in the recipe table"
    return table


# The one canonical table every craft()/cook() call looks up against.
RECIPES: dict[str, Recipe] = build_recipe_table()


def recipes_for_tool(tool: ProductionTool) -> list[Recipe]:
    return [r for r in RECIPES.values() if r.tool is tool]
