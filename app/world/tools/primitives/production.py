"""craft, cook: both dispatch the same recipe table (recipes.py), each
restricted to its own `ProductionTool` tag so a `cook` call can never
smelt ore and a `craft` call can never bake bread. DESIGN.md section 2:
"craft (recipe-based conversions: e.g. grain->flour->bread)" - the recipe
IS the location gate (a wheat-to-flour conversion requires the mill,
cotton-thread-to-cloth requires the loom house), which is why both tools'
`location_gate` is a resolver over the chosen recipe rather than one
fixed location.
"""

from __future__ import annotations

from pydantic import BaseModel

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.tools.errors import RecipeNotFoundError
from world.tools.recipes import RECIPES, ProductionTool, Recipe
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


def require_recipe(recipe_id: str, tool: ProductionTool) -> Recipe:
    recipe = RECIPES.get(recipe_id)
    if recipe is None or recipe.tool is not tool:
        # The ids THIS tool accepts. A bare "no recipe 'potato_soup'" tells
        # an agent its guess was wrong and leaves it nothing better to guess
        # next, so the next turn goes on another invention; the list is the
        # correction. Scoped to the tool that was called, because that is
        # the question actually being asked - `cook` has no business
        # advertising what the sawmill makes.
        raise RecipeNotFoundError(
            recipe_id,
            [rid for rid, r in RECIPES.items() if r.tool is tool],
        )
    return recipe


def run_recipe(world: WorldState, agent: AgentLifeRecord, recipe: Recipe, batches: int) -> ToolOutcome:
    total_input = recipe.input_qty * batches
    total_output = recipe.output_qty * batches
    # Debit-then-credit, in that order: an insufficient-input raise here
    # happens before anything is produced, so a failed craft never
    # conjures output from nothing (Inventory.debit already enforces
    # this at the single-call level; this only matters because craft
    # does a debit and a credit in the same handler).
    world.inventory.debit(agent.agent_id, recipe.input_item, total_input)
    world.inventory.credit(agent.agent_id, recipe.output_item, total_output)

    event = WorldEvent(
        type="world.goods.crafted" if recipe.tool is ProductionTool.CRAFT else "world.goods.cooked",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "recipe_id": recipe.recipe_id,
            "input_item": recipe.input_item,
            "input_qty": total_input,
            "output_item": recipe.output_item,
            "output_qty": total_output,
            "target_agent_id": agent.agent_id,
        },
    )
    return ToolOutcome(
        result={"output_item": recipe.output_item, "output_qty": total_output}, events=[event]
    )


class CraftParams(BaseModel):
    recipe_id: str
    batches: int = 1


async def craft_handler(world: WorldState, agent: AgentLifeRecord, params: CraftParams) -> ToolOutcome:
    recipe = require_recipe(params.recipe_id, ProductionTool.CRAFT)
    return run_recipe(world, agent, recipe, params.batches)


class CookParams(BaseModel):
    recipe_id: str
    batches: int = 1


async def cook_handler(world: WorldState, agent: AgentLifeRecord, params: CookParams) -> ToolOutcome:
    recipe = require_recipe(params.recipe_id, ProductionTool.COOK)
    return run_recipe(world, agent, recipe, params.batches)


def craft_location_gate(world: WorldState, params: CraftParams) -> str:
    return require_recipe(params.recipe_id, ProductionTool.CRAFT).required_location


def cook_location_gate(world: WorldState, params: CookParams) -> str:
    return require_recipe(params.recipe_id, ProductionTool.COOK).required_location



def _recipe_menu(tool: ProductionTool) -> str:
    """Every recipe this verb accepts, as `input->output @place`.

    TOOL-14. The description was "Convert raw material into a processed
    good via a recipe" and named not one. Run 23: 18 of 23 `craft` calls
    refused, 13 of them on ids the model invented from that sentence -
    `bread`, `wheat->flour`, `planks`, `oak_planks`, `timber_to_planks`,
    `mill_flour`, `mill:mill_flour_run`. Both `cook` calls refused the same
    way.

    The refusal already lists the real ids (`errors.py:165-185`), which is
    right and comes one paid turn too late. Twenty-eight recipes is about
    150 tokens of description against a turn that costs cents - the
    cheapest correction in the town.
    """
    by_place: dict[str, list[str]] = {}
    for recipe in RECIPES.values():
        if recipe.tool is tool:
            by_place.setdefault(recipe.required_location, []).append(recipe.recipe_id)
    # Grouped by workshop rather than listed flat: the id already carries
    # the arrow ("craft:wheat->flour"), so repeating input and output per
    # row doubled the length for nothing, and where a recipe works is the
    # fact the resident is missing.
    return "; ".join(
        f"at the {place}: {', '.join(sorted(ids))}"
        for place, ids in sorted(by_place.items())
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="craft",
            description=(
                "Convert raw material into a processed good, one recipe at a "
                "time, standing in the workshop that recipe names. A business "
                "of the right trade provides its workshop: the mill is a "
                "mill, the bakery is a kitchen. Recipes: "
                + _recipe_menu(ProductionTool.CRAFT)
                + ". Cost: 6 sim plus 8 per batch, less with practice."
            ),
            params_model=CraftParams,
            handler=craft_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=craft_location_gate,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="cook",
            description=(
                "Cook a milled good or fresh produce into a finished dish, "
                "standing in a kitchen. A bakery, cafe, inn or tavern is a "
                "kitchen. Recipes: "
                + _recipe_menu(ProductionTool.COOK)
                + ". Cost: 5 sim plus 6 per batch, less with practice."
            ),
            params_model=CookParams,
            handler=cook_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=cook_location_gate,
            actor_requirements=ActorRequirements(),
        ),
    ]
