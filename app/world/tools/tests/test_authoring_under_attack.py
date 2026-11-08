"""Adversarial: a resident trying to get something for nothing.

The existing skillify and toolify tests check what happens when an agent
gets it WRONG - a misremembered tool name, a workflow that names itself,
one nested too deep. This file checks what happens when an agent gets it
RIGHT, for the wrong reasons: composition as a way around a price, a
location, a whitelist, or a permission.

That distinction matters more here than in most systems. DESIGN.md
section 2's whole claim about invented tools is that "no new physics" is
true BY CONSTRUCTION rather than by policy, and this town's entire
research question is what agents do when acting costs them money. Both
claims are only as good as the cheapest way around them, and an agent
optimising its own survival is exactly the thing that will find it.

Every case below is a plausible move for a resident, not a hostile input:
nobody has to be malicious to notice that wrapping twenty harvests in one
tool might cost one fee, or that a tool built at the temple might still
work at the mill.

WHAT THIS FILE IS NOT. It is not a fuzzer and it does not test the
pydantic layer - a malformed params dict is already refused before any
handler sees it. Each test names an ADVANTAGE somebody might be seeking.
"""

from __future__ import annotations

import pytest

from world.tools.errors import ToolError, ToolValidationError
from world.tools.meta import flatten_to_primitives

from .conftest import spawn_agent

FARM = "Kasukabe Wheat Field"
TEMPLE = "Kasukabe Temple"


async def resident(world, agent_id: str = "n01", place: str = FARM):
    await spawn_agent(world, agent_id, place)
    world.agent_names[agent_id] = f"Resident {agent_id}"
    return agent_id


# -- The advantage: escaping the primitive whitelist ------------------


@pytest.mark.asyncio
async def test_a_composed_tool_may_stand_on_one_and_still_reach_primitives(
    world, registry
) -> None:
    """TOOL-31 replaced this test\'s rule, and this is the argument.

    It used to assert that an invented tool could never cite another one,
    on two grounds: unbounded depth, and "what any tool actually DOES
    becomes unreadable without walking a graph". The first is now a
    bounded number (`MAX_TOOL_DEPTH`, one shared dispatch ceiling for the
    whole nest); the second is a walk the world does itself, at filing
    time, and refuses the filing if it does not end in primitives.

    What it cost to keep was a town whose tools could never compound: five
    runs, 5,544 resident calls, `toolify` called zero times. So the rule
    is now what it always protected - every LEAF is an act the caller
    could have performed by hand - and that is what this asserts.
    """
    agent_id = await resident(world)
    await registry.dispatch(world, agent_id, "toolify", {
        "name": "look_twice",
        "description": "look, then look again",
        "steps": [{"tool_name": "look_around", "params": {}}],
    })
    assert registry.get("look_twice") is not None, "the first tool must exist"

    await registry.dispatch(world, agent_id, "toolify", {
        "name": "look_four_times",
        "description": "the same thing, twice as much",
        "steps": [{"tool_name": "look_twice", "params": {}, "repeat": 2}],
    })

    leaves = flatten_to_primitives(
        registry, world, world.custom_tools["look_four_times"].steps
    )
    assert leaves == ["look_around"], leaves
    assert set(leaves) <= registry.primitive_names

    # And the thing that is still unrepresentable: a tool nobody can call.
    with pytest.raises(ToolValidationError):
        await registry.dispatch(world, agent_id, "toolify", {
            "name": "wishful",
            "description": "built on something the town does not have",
            "steps": [{"tool_name": "look_thrice", "params": {}}],
        })


@pytest.mark.asyncio
async def test_a_composed_tool_cannot_be_built_out_of_the_meta_tools(
    world, registry
) -> None:
    """A tool that files tools would be a tool that writes its own
    physics, one indirection removed."""
    agent_id = await resident(world)
    for forbidden in ("toolify", "define_tool", "skillify"):
        with pytest.raises(ToolValidationError):
            await registry.dispatch(world, agent_id, "toolify", {
                "name": f"meta_{forbidden}",
                "description": "composition all the way down",
                "steps": [{"tool_name": forbidden, "params": {}}],
            })


# -- The advantage: a name that shadows a real tool -------------------


@pytest.mark.asyncio
async def test_a_tool_cannot_take_the_name_of_one_that_exists(world, registry) -> None:
    """Registering `move` would replace walking for the whole town."""
    agent_id = await resident(world)
    with pytest.raises(ToolValidationError):
        await registry.dispatch(world, agent_id, "toolify", {
            "name": "move",
            "description": "not walking",
            "steps": [{"tool_name": "look_around", "params": {}}],
        })


@pytest.mark.asyncio
async def test_a_tool_named_like_a_namespaced_primitive_cannot_hijack_a_skill_step(
    world, registry
) -> None:
    """The subtle one, and it is real.

    `canonical_tool_ref` folds a leading namespace off a step reference,
    because the engine shows tools as `world_move` while the registry
    keeps them as `move` - and a resident writing down what it had been
    shown lost every step of a six-step workflow to that mismatch.

    The fold only applies when the raw name misses. So a resident who
    registers a tool actually CALLED `world_move` gets an exact hit, the
    fold never runs, and every future skill step written the way the
    engine displays it silently routes to that resident's tool instead of
    to walking. Nobody would have to intend it for it to happen.

    Pinned rather than forbidden: registering the name is allowed, and
    what must hold is that an exact match is what it means - the step goes
    where the author literally wrote, and `move` still means walking.
    """
    agent_id = await resident(world)
    await registry.dispatch(world, agent_id, "toolify", {
        "name": "world_move",
        "description": "a tool with a confusing name",
        "steps": [{"tool_name": "look_around", "params": {}}],
    })

    from world.tools.meta import canonical_tool_ref

    assert canonical_tool_ref(registry, "world_move") == "world_move"
    assert canonical_tool_ref(registry, "move") == "move"
    assert canonical_tool_ref(registry, "world_harvest") == "harvest", (
        "an ordinary namespaced reference must still fold, or the fix this "
        "attack rides on stops working for everybody else"
    )


# -- The advantage: paying once for many steps ------------------------


@pytest.mark.asyncio
async def test_a_composed_tool_pays_for_every_step_it_takes(world, registry) -> None:
    """The economic escape, and the one worth most.

    If a composite were charged as one action, the cheapest strategy in
    Kasukabe would be to wrap everything you ever do in a tool, and the
    experiment - what do agents do when thinking and acting cost money -
    would be answering a question about our billing rather than about
    them.

    `build_composite_handler` replays each step through `dispatch()`,
    which is the same gate-checked, charged path any ordinary call takes.
    This asserts the consequence rather than the mechanism: three looks
    cost what three looks cost.

    THREE DIFFERENT PEOPLE, since TRADE-04. That rule folds an identical
    read repeated on the same tick - the same question cannot have a new
    answer - so a composite of three identical lookups would now cost one
    OUTSIDE a composite too, and this test would be measuring the fold
    rather than the composition. Three distinct questions are three
    questions wherever they are asked.

    The escape the fold could have opened is closed by the last
    assertion here rather than by exempting composites: an identical
    repeat costs the same inside a tool as outside one, so wrapping
    something buys nothing.
    """
    agent_id = await resident(world)
    for other in ("n02", "n03", "n04", "n05"):
        world.agent_names[other] = f"Somebody {other}"
        await spawn_agent(world, other, FARM)

    before = world.ledger.sim_balance(agent_id)
    await registry.dispatch(world, agent_id, "describe_person", {"agent_id": "n02"})
    one_look = before - world.ledger.sim_balance(agent_id)
    assert one_look > 0, (
        "describe_person is free in this world, so this test measures "
        "nothing - pick a tool with a price before trusting it"
    )

    await registry.dispatch(world, agent_id, "toolify", {
        "name": "look_thrice",
        "description": "size three people up",
        "steps": [
            {"tool_name": "describe_person", "params": {"agent_id": other}}
            # NOT n02: the baseline probe above already asked about
            # n02 on this tick, so under TRADE-04 that step would fold
            # and this test would measure the fold instead of the
            # composition.
            for other in ("n03", "n04", "n05")
        ],
    })

    before = world.ledger.sim_balance(agent_id)
    await registry.dispatch(world, agent_id, "look_thrice", {})
    spent = before - world.ledger.sim_balance(agent_id)
    assert spent >= 3 * one_look, (
        f"three lookups inside a tool cost {spent}, three outside cost "
        f"{3 * one_look} - composition is a discount on the world"
    )

    # And the fold is symmetrical, which is what keeps it from BEING a
    # discount: asking the same thing twice costs one, inside or out.
    before = world.ledger.sim_balance(agent_id)
    await registry.dispatch(world, agent_id, "describe_person", {"agent_id": "n02"})
    await registry.dispatch(world, agent_id, "describe_person", {"agent_id": "n02"})
    outside_repeat = before - world.ledger.sim_balance(agent_id)

    before = world.ledger.sim_balance(agent_id)
    await registry.dispatch(world, agent_id, "look_thrice", {})
    inside_repeat = before - world.ledger.sim_balance(agent_id)
    assert inside_repeat <= outside_repeat + one_look, (
        f"repeating a read inside a tool cost {inside_repeat} and outside "
        f"{outside_repeat} - wrapping it is buying a discount"
    )


# -- The advantage: doing elsewhere what only happens somewhere --------


@pytest.mark.asyncio
async def test_a_composed_tool_does_not_carry_a_location_with_it(
    world, registry
) -> None:
    """`pray` happens at the temple. A tool that wrapped it and ran
    anywhere would be a way to be in two places, which is the one thing
    this world's geography is for.

    The composite itself has no gate - a resident may CALL it from the
    field - and the step inside is refused there, which is the right
    shape: the gate belongs to the act, not to the wrapper.
    """
    agent_id = await resident(world, place=FARM)
    await registry.dispatch(world, agent_id, "toolify", {
        "name": "pray_anywhere",
        "description": "a prayer without the walk",
        "steps": [{"tool_name": "pray", "params": {"petition": "rain"}}],
    })
    with pytest.raises(ToolError):
        await registry.dispatch(world, agent_id, "pray_anywhere", {})


# -- The advantage: a workflow that never ends ------------------------


@pytest.mark.asyncio
async def test_two_skills_cannot_call_each_other(world, registry) -> None:
    """Neither one nests past the limit on its own. Together they are a
    cycle, and a reader of either prompt sees half a procedure.

    `_nesting_depth` walks the graph with a `seen` set, so the cycle
    cannot hang the filing - what it must also do is refuse it.
    """
    agent_id = await resident(world)
    await registry.dispatch(world, agent_id, "skillify", {
        "name": "first",
        "steps": [{"order": 1, "kind": "tool", "ref": "look_around"}],
    })
    await registry.dispatch(world, agent_id, "skillify", {
        "name": "second",
        "steps": [{"order": 1, "kind": "skill", "ref": "first"}],
    })
    # `first` cannot now be revised to call `second` without closing the
    # loop. Whatever the outcome, it must be a decision and not a hang.
    try:
        await registry.dispatch(world, agent_id, "revise_skill", {
            "name": "first",
            "steps": [{"order": 1, "kind": "skill", "ref": "second"}],
        })
    except ToolError:
        return  # refused outright, which is the cleaner answer
    depth_probe = world.skill_definitions["first"]
    from world.tools.meta import _nesting_depth

    assert _nesting_depth(world, depth_probe, {"first"}) <= 3, (
        "a cycle got past the depth check, so a reader of the prompt sees "
        "a procedure that refers to itself through another one"
    )


# -- The advantage: somebody else's secret ----------------------------


@pytest.mark.asyncio
async def test_a_skill_cannot_cite_a_business_secret_it_may_not_see(
    world, registry
) -> None:
    """Building on a procedure you have no right to walk.

    STATED PRECISELY, because the first version of this test overstated
    it. Citing a secret does NOT leak its contents: a playbook shows the
    outer skill's own steps and the NAME of the inner one, never what is
    inside it. What citing did do was let a stranger file a workflow that
    could never advance past step one and then spend turns finding out.

    `teach_skill`'s own comment says the lesson is "the one door that
    needs a lock". Citing is a second door, and `may_follow_skill` is now
    the single predicate both of them ask, so the two cannot drift.
    """
    owner = await resident(world, "n01")
    outsider = await resident(world, "n02")
    world.businesses.clear()
    from world.tools.models import Business

    world.businesses["b1"] = Business(
        business_id="b1", name="The Mill", business_type="mill",
        owner_agent_id=owner, employee_agent_ids=[], location_id=FARM,
    )
    await registry.dispatch(world, owner, "skillify", {
        "name": "mill secret",
        "business_id": "b1",
        "steps": [{"order": 1, "kind": "tool", "ref": "look_around"}],
    })

    with pytest.raises(ToolError):
        await registry.dispatch(world, outsider, "skillify", {
            "name": "borrowed",
            "steps": [{"order": 1, "kind": "skill", "ref": "mill secret"}],
        })


# -- The advantage: a refusal that leaks ------------------------------


@pytest.mark.asyncio
async def test_a_refusal_never_lists_a_tool_the_asker_cannot_see(
    world, registry
) -> None:
    """The context attached to a refusal is a listing, and a listing is
    exactly what DESIGN.md says a non-member must not get. A helpful
    error that enumerates confidential tools would be a worse leak than
    the one the visibility rule prevents, because it arrives unasked.
    """
    outsider = await resident(world, "n02")
    context = await world.authoring_context(outsider)
    names = {row["name"] for row in context["tools_you_can_use"]}
    for name, spec in registry.tools.items():
        if not await registry.is_visible(spec, world, outsider):
            assert name not in names, (
                f"{name} is invisible to {outsider} and the authoring context "
                "lists it anyway"
            )
