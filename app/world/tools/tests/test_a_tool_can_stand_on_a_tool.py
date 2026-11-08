"""An invented tool may be built on an invented tool - and the leaves stay primitives.

Runs 25-28b: `toolify` was called 0 times by 0 residents across 5,544
resident calls, and `skillify` three times. One reason is in the refusal
itself - `PrimitiveNotWhitelistedError` told an inventor it "may only
compose primitives that existed at bootstrap, never another invented
tool" - so the town's invented tools could never become a library. A
resident that had written `look_here` could not write `find_food` on top
of it; it had to restate the primitives every time, in every tool, and a
tool that cannot stand on another tool is a tool nobody builds with.

"No new physics possible by construction" is NOT what the old refusal
protected, and this file is the argument. A composition is a tree; the
rule that matters is that every LEAF is a primitive the caller could
have called one at a time. Flattening proves that for a tower the same
way the whitelist proved it for one level, and the tower is bounded:
`MAX_TOOL_DEPTH` deep, `PROGRAM_MAX_STEPS` leaves, one shared dispatch
and wallet ceiling for the whole nest, no cycles.
"""

from __future__ import annotations

import pytest

from world.tools.meta import MAX_TOOL_DEPTH
from world.tools.program import PROGRAM_MAX_CALLS
from world.tools.tests.conftest import spawn_agent

WHERE = "Kasukabe Farmland"


async def _toolify(registry, world, agent: str, name: str, steps: list[dict], schema: dict | None = None):
    return await registry.dispatch(world, agent, "toolify", {
        "name": name,
        "description": f"{name}, for the test",
        "param_schema": schema or {},
        "steps": steps,
    })


@pytest.mark.asyncio
async def test_an_invented_tool_may_be_a_step_of_another(world, registry):
    """The `search_food` shape: one tool wraps the tool below it."""
    await spawn_agent(world, "n01", WHERE)
    await _toolify(registry, world, "n01", "look_here", [{"tool_name": "look_around", "params": {}}])

    await _toolify(registry, world, "n01", "scout", [{"tool_name": "look_here", "params": {}}])

    assert "scout" in world.custom_tools
    outcome = await registry.dispatch(world, "n01", "scout", {})
    assert outcome.result["steps_executed"] == 1


@pytest.mark.asyncio
async def test_every_leaf_of_a_tower_is_still_a_primitive(world, registry):
    """What the old whitelist actually protected, now proven by flattening."""
    from world.tools.meta import flatten_to_primitives

    await spawn_agent(world, "n01", WHERE)
    await _toolify(registry, world, "n01", "look_here", [{"tool_name": "look_around", "params": {}}])
    await _toolify(registry, world, "n01", "scout", [
        {"tool_name": "look_here", "params": {}},
        {"tool_name": "rest", "params": {}},
    ])

    leaves = flatten_to_primitives(registry, world, world.custom_tools["scout"].steps)
    assert leaves == ["look_around", "rest"]
    assert set(leaves) <= registry.primitive_names


@pytest.mark.asyncio
async def test_a_step_naming_nothing_the_town_has_is_still_refused(world, registry):
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", WHERE)
    with pytest.raises(ToolValidationError) as raised:
        await _toolify(registry, world, "n01", "wishful", [{"tool_name": "teleport", "params": {}}])
    assert "teleport" in str(raised.value)


@pytest.mark.asyncio
async def test_the_tower_has_a_ceiling(world, registry):
    """A tool may stand on a tool, not on an arbitrarily deep tower: the
    filing is refused and the refusal says how deep it may go."""
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", WHERE)
    name = "floor_0"
    await _toolify(registry, world, "n01", name, [{"tool_name": "look_around", "params": {}}])
    for level in range(1, MAX_TOOL_DEPTH + 1):
        nxt = f"floor_{level}"
        await _toolify(registry, world, "n01", nxt, [{"tool_name": name, "params": {}}])
        name = nxt

    with pytest.raises(ToolValidationError) as raised:
        await _toolify(registry, world, "n01", "one_too_high", [{"tool_name": name, "params": {}}])
    assert str(MAX_TOOL_DEPTH) in str(raised.value)


@pytest.mark.asyncio
async def test_a_tower_shares_one_dispatch_ceiling(world, registry):
    """Nesting must not multiply the ceiling. Ten reads inside a tool,
    five of those inside another, is fifty trips through the registry -
    and the ceiling is forty for the whole nest, not per level."""
    await spawn_agent(world, "n01", WHERE)
    await _toolify(registry, world, "n01", "ten_looks", [
        {"tool_name": "look_around", "params": {}, "repeat": 10},
    ])
    await _toolify(registry, world, "n01", "fifty_looks", [
        {"tool_name": "ten_looks", "params": {}} for _ in range(5)
    ])

    outcome = await registry.dispatch(world, "n01", "fifty_looks", {})

    assert outcome.result.get("stopped_early") == "calls", outcome.result
    assert outcome.result["calls_made"] + outcome.result.get("repeats_folded", 0) <= PROGRAM_MAX_CALLS


@pytest.mark.asyncio
async def test_a_tool_cannot_stand_on_one_it_cannot_see(world, registry):
    """A trade secret is a secret from the composition layer too: citing
    another business's private tool is refused the way an unknown name is."""
    from world.tools.errors import ToolValidationError
    from world.tools.models import Business

    await spawn_agent(world, "n01", WHERE)
    await spawn_agent(world, "n02", WHERE)
    world.businesses["b_secret"] = Business(
        business_id="b_secret", name="Vance Mill", business_type="mill",
        owner_agent_id="n01", location_id=WHERE,
    )
    await world.ledger.register_business("b_secret", "n01", fee_sim=1, tick=world.tick())
    await registry.dispatch(world, "n01", "define_business_tool", {
        "business_id": "b_secret",
        "name": "secret_look",
        "description": "the mill's own way of looking",
        "param_schema": {},
        "steps": [{"tool_name": "look_around", "params": {}}],
    })

    with pytest.raises(ToolValidationError) as raised:
        await _toolify(registry, world, "n02", "borrowed", [{"tool_name": "secret_look", "params": {}}])
    assert "secret_look" in str(raised.value)


@pytest.mark.asyncio
async def test_a_tool_cannot_come_to_stand_on_itself(world, registry):
    """`revise_tool` is the only way a cycle could be filed: A cites B,
    then B is revised to cite A. Refused, naming the loop."""
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "n01", WHERE)
    await _toolify(registry, world, "n01", "below", [{"tool_name": "look_around", "params": {}}])
    await _toolify(registry, world, "n01", "above", [{"tool_name": "below", "params": {}}])

    with pytest.raises(ToolValidationError) as raised:
        await registry.dispatch(world, "n01", "revise_tool", {
            "name": "below",
            "steps": [{"tool_name": "above", "params": {}}],
        })
    assert "below" in str(raised.value)


@pytest.mark.asyncio
async def test_a_tower_costs_exactly_its_leaves(world, registry):
    """The economic property, and the one worth most.

    If each layer charged a wrapper fee, the cheapest strategy in Kasukabe
    would be to keep your tools flat, and a town told to build a library
    would be paying a tax for doing it. TOOL-29 found exactly that charge
    once already (an invented tool's own name fell to the 5-sim default
    rule), so it is pinned here rather than assumed.
    """
    from world.tools.tests.conftest import action_cost

    await spawn_agent(world, "n01", WHERE)
    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "rest", {})
    by_hand = before - world.ledger.sim_balance("n01")

    await _toolify(registry, world, "n01", "inner", [{"tool_name": "rest", "params": {}}])
    await _toolify(registry, world, "n01", "outer", [{"tool_name": "inner", "params": {}}])
    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "outer", {})
    two_layers_deep = before - world.ledger.sim_balance("n01")

    assert by_hand == action_cost("rest")
    assert two_layers_deep == by_hand, (
        "standing a tool on a tool charged something extra, so a library costs "
        "more than the acts in it"
    )
