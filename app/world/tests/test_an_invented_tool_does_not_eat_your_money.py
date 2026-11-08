"""A composite that fails halfway gives back what its first steps paid.

`TOOL-06`, the money leak. `registry.dispatch` charges each step and
refunds only the step that RAISED. A composite tool runs its steps
through `dispatch` inside a single `execute`, and `execute` restores
tools-owned state on an exception - so Junia's five-step `mill_flour_run`
failing at step 3 had paid for `withdraw_item` and `move` while the
world said neither had happened. The comment at the charge site says in
so many words that this is the case that must not occur; nothing stopped
it, because the ledger is a sibling system and is not in the snapshot.

And a second leak beside it. An invented tool's own name has no
`SCHEDULE_TABLE` row, so `rule_for` fell through to `DEFAULT_RULE` and
took 5 sim for the WRAPPER on top of every step the composite paid for
itself - which contradicts `toolify`'s own description, "Each step still
costs what it always did", in a way a resident could only discover by
watching its balance.

Both are tested here through the real registry, because both were
invisible to every test that called a primitive directly.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.errors import ToolValidationError
from world.tools.models import CustomToolDefinition, ToolStep
from world.tools.registry import ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


@pytest_asyncio.fixture
async def town():
    from world.life.models import AgentLifeRecord, Sex

    world, registry = await build_world(genesis_tick=0, seed=11)
    world.agents["a_maker"] = AgentLifeRecord(
        agent_id="a_maker", age_years=30.0, sex=Sex.FEMALE, household_id="h1"
    )
    world.ledger.open_agent("a_maker", starting_usd_cents=500, starting_sim=5_000)
    return world, registry


def _priced_step(world, registry, name: str, *, cost: int, fails: bool = False):
    """A primitive with a known price, so the arithmetic is checkable.

    Priced into `world.action_costs`, not the module-level
    SCHEDULE_TABLE: the schedule object is built at boot from a copy, so
    mutating the table afterwards changes nothing the registry reads -
    which is how the first version of this test came to assert 5 == 5
    and think it had proved something.
    """
    from pydantic import BaseModel

    from world.ledger.action_costs import ActionCostCategory, ActionCostRule

    class NoParams(BaseModel):
        pass

    async def handler(world, agent, params) -> ToolOutcome:
        if fails:
            raise ToolValidationError(name, ["this step always fails"])
        return ToolOutcome(result={"did": name}, events=[])

    world.action_costs.rules[name] = ActionCostRule(
        category=ActionCostCategory.LABOUR, base_cost=cost, min_cost=cost,
        description="test",
    )
    registry.register(ToolSpec(
        name=name, description="test step", params_model=NoParams, handler=handler,
        namespace=ToolNamespace.PUBLIC, location_gate=None,
    ))


def _invent(world: WorldState, registry, name: str, steps: list[str]) -> None:
    """File an invented tool AND wire it, the way the patent office does.

    Approved on the spot: the patent path is `meta.py`'s business and
    this file is about what a composite COSTS.
    """
    from world.tools.meta import build_composite_handler, build_params_model
    from world.tools.registry import ActorRequirements

    world.custom_tools[name] = CustomToolDefinition(
        tool_name=name,
        creator_agent_id="a_maker",
        description="a made-up routine",
        steps=[ToolStep(tool_name=s, params={}) for s in steps],
        created_tick=0,
        approved=True,
    )
    registry.register(ToolSpec(
        name=name,
        description="a made-up routine",
        params_model=build_params_model(name, {}),
        handler=build_composite_handler(registry, name),
        namespace=ToolNamespace.PUBLIC,
        location_gate=None,
        actor_requirements=ActorRequirements(),
        is_primitive=False,
    ))


@pytest.mark.asyncio
async def test_a_composite_that_fails_at_step_three_refunds_steps_one_and_two(town):
    world, registry = town
    _priced_step(world, registry, "t_step_one", cost=30)
    _priced_step(world, registry, "t_step_two", cost=20)
    _priced_step(world, registry, "t_step_three", cost=10, fails=True)
    _invent(world, registry, "t_routine", ["t_step_one", "t_step_two", "t_step_three"])


    before = world.ledger.sim_balance("a_maker")
    with pytest.raises(Exception):
        await registry.execute(world, "a_maker", "t_routine", {})

    assert world.ledger.sim_balance("a_maker") == before, (
        "a five-step tool failed at step 3 and the resident paid for "
        "steps 1 and 2 of an act the world says never happened"
    )


@pytest.mark.asyncio
async def test_a_composite_that_succeeds_still_pays_for_its_steps(town):
    """The other direction: refunding on failure must not refund on success."""
    world, registry = town
    _priced_step(world, registry, "t_ok_one", cost=30)
    _priced_step(world, registry, "t_ok_two", cost=20)
    _invent(world, registry, "t_good", ["t_ok_one", "t_ok_two"])


    before = world.ledger.sim_balance("a_maker")
    await registry.execute(world, "a_maker", "t_good", {})

    assert world.ledger.sim_balance("a_maker") == before - 50


@pytest.mark.asyncio
async def test_the_wrapper_itself_is_free(town):
    """`toolify`: "Each step still costs what it always did." No 5-sim
    DEFAULT_RULE fee for the invented name on top."""
    world, registry = town
    _priced_step(world, registry, "t_solo", cost=30)
    _invent(world, registry, "t_wrapper", ["t_solo"])


    before = world.ledger.sim_balance("a_maker")
    await registry.execute(world, "a_maker", "t_wrapper", {})
    through_the_wrapper = before - world.ledger.sim_balance("a_maker")

    after = world.ledger.sim_balance("a_maker")
    await registry.execute(world, "a_maker", "t_solo", {})
    direct = after - world.ledger.sim_balance("a_maker")

    assert through_the_wrapper == direct == 30, (
        f"the wrapper cost {through_the_wrapper - direct} sim more than "
        "calling its one step directly"
    )


@pytest.mark.asyncio
async def test_a_plain_primitive_that_fails_is_still_refunded_once(town):
    """The single-step path, which already worked - held so the new
    bookkeeping cannot start refunding it twice."""
    world, registry = town
    _priced_step(world, registry, "t_lone_fail", cost=40, fails=True)

    before = world.ledger.sim_balance("a_maker")
    with pytest.raises(Exception):
        await registry.execute(world, "a_maker", "t_lone_fail", {})

    assert world.ledger.sim_balance("a_maker") == before, (
        "a failed call was double-refunded or not refunded at all"
    )


@pytest.mark.asyncio
async def test_the_charge_ledger_does_not_leak_between_calls(town):
    """A call's charges must not be refundable by the NEXT call's failure."""
    world, registry = town
    _priced_step(world, registry, "t_paid", cost=25)
    _priced_step(world, registry, "t_boom", cost=15, fails=True)

    await registry.execute(world, "a_maker", "t_paid", {})
    after_paying = world.ledger.sim_balance("a_maker")

    with pytest.raises(Exception):
        await registry.execute(world, "a_maker", "t_boom", {})

    assert world.ledger.sim_balance("a_maker") == after_paying, (
        "a later failure refunded an earlier, successful call"
    )
    assert world.charges_this_call == []


@pytest.mark.asyncio
async def test_a_step_naming_a_recipe_that_does_not_exist_is_refused_at_filing(town):
    """Junia's `mill_flour_run`, in its own terms.

    `craft(recipe_id="mill_flour_from_wheat")` was accepted by
    `validate_steps` - the step named a whitelisted primitive and had no
    placeholders, which was the whole check - and failed at step 3 on
    its first call, after `withdraw_item` and `move` had already run and
    been paid for. The inventor spent the invention fee, the patent
    office approved it, and the resident found out by watching a turn
    die halfway through.
    """
    from world.tools.meta import validate_steps

    world, registry = town
    with pytest.raises(ToolValidationError) as caught:
        validate_steps(
            registry,
            {},
            [ToolStep(tool_name="craft", params={"recipe_id": "mill_flour_from_wheat"})],
        )
    said = str(caught.value)
    assert "mill_flour_from_wheat" in said
    assert "no recipe" in said
    assert "Some that exist:" in said


@pytest.mark.asyncio
async def test_a_step_missing_a_required_parameter_is_refused_at_filing(town):
    from world.tools.meta import validate_steps

    world, registry = town
    with pytest.raises(ToolValidationError) as caught:
        validate_steps(registry, {}, [ToolStep(tool_name="craft", params={})])
    assert "step 0 (craft)" in str(caught.value)


@pytest.mark.asyncio
async def test_a_templated_step_is_not_second_guessed(town):
    """A step whose params arrive at call time cannot be validated now,
    and refusing it would reject a perfectly good composition."""
    from world.tools.meta import validate_steps

    world, registry = town
    validate_steps(
        registry,
        {"recipe_id": "string"},
        [ToolStep(tool_name="craft", params={"recipe_id": "$recipe_id"})],
    )


@pytest.mark.asyncio
async def test_a_good_composition_still_files(town):
    """The guard must refuse bad tools, not all tools."""
    from world.tools.meta import validate_steps
    from world.tools.recipes import RECIPES

    world, registry = town
    real = next(rid for rid, r in RECIPES.items() if r.tool.value == "craft")
    validate_steps(registry, {}, [ToolStep(tool_name="craft", params={"recipe_id": real})])


@pytest.mark.asyncio
async def test_every_problem_in_one_refusal(town):
    """CONVENTIONS.md's aggregate-errors idiom: an inventor sees
    everything wrong at once, not the first thing."""
    from world.tools.meta import validate_steps

    world, registry = town
    with pytest.raises(ToolValidationError) as caught:
        validate_steps(
            registry,
            {},
            [
                ToolStep(tool_name="craft", params={"recipe_id": "not_a_recipe"}),
                ToolStep(tool_name="cook", params={"recipe_id": "also_not_a_recipe"}),
            ],
        )
    said = str(caught.value)
    assert "not_a_recipe" in said and "also_not_a_recipe" in said
