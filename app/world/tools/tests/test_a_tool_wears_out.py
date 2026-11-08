"""What the smith makes, the town uses up.

`GROW-23`. `recipes.py` outputs planks, furniture, cloth, `{metal}_ingot`,
`iron_tool`, `copper_wire`, `tin_ware`, `silver_jewelry`,
`gold_jewelry`. Exactly one of those chains had a consumer: crops become
food and `eat` consumes it. Everything else accumulated for ever.

"Tool wear" existed only as a sim-currency fee in `action_costs.py` - a
number taken out of a wallet with no item behind it. So the artisanry
proficiency Silas, Junia and Tobias built over run 23 converted into
nothing anybody needed: they could make an `iron_tool`, and no resident
in the town, themselves included, had any reason to want one.

Two halves, and both are needed. A tool that wears out but costs nothing
to be without is a tax on whoever bothers to own one; a discount with no
consumption is a permanent subsidy to whoever buys one first.

WHAT THIS DOES NOT DO, stated rather than half-done. The review's fix
also makes buildings consume planks and ingots, and adds building
maintenance with a `repair_site` verb. The buildings half belongs to
GROW-08, which owns the duplicate bills of materials - implementing it
here would put a second construction of the same bill in the tree, which
is the defect GROW-08 is about. `furniture`, `cloth`, `copper_wire`,
`tin_ware` and the two jewelleries still have no sink.
"""
from __future__ import annotations

import pytest

from world.ledger.action_costs import ActionActor, cost_for
from world.tools import wear

from .conftest import move_agent  # noqa: F401
from .test_construction import spawn_agent  # noqa: F401


class _Ground:
    """Only what `wear_one_use` touches. Narrow on purpose: a new
    dependency should raise here, not be silently satisfied."""

    def __init__(self, tools: int = 0) -> None:
        self.tool_wear: dict[str, int] = {}

        class _Inv:
            def __init__(self) -> None:
                self.held = {"a1": tools}

            def quantity(self, agent_id, item):
                return self.held.get(agent_id, 0) if item == wear.TOOL_ITEM else 0

            def debit(self, agent_id, item, n):
                self.held[agent_id] = self.held.get(agent_id, 0) - n

        self.inventory = _Inv()


def test_only_manual_work_wears_a_tool() -> None:
    """`move` and `buy` do not use a chisel. Wearing on every verb would
    make a tool a tax on existing."""
    assert wear.wears_a_tool("harvest")
    assert wear.wears_a_tool("craft")
    assert wear.wears_a_tool("work_site"), "GROW-18's verb is manual work too"
    assert not wear.wears_a_tool("move")
    assert not wear.wears_a_tool("buy")
    assert not wear.wears_a_tool("say")


def test_a_tool_survives_a_run_of_work_and_then_breaks() -> None:
    world = _Ground(tools=1)
    for _ in range(wear.USES_PER_TOOL - 1):
        assert wear.wear_one_use(world, "a1", "harvest") is False
    assert wear.wear_one_use(world, "a1", "harvest") is True, (
        f"GROW-23: {wear.USES_PER_TOOL} acts of manual work consumed no "
        "tool, so iron_tool still has no sink"
    )
    assert world.inventory.quantity("a1", wear.TOOL_ITEM) == 0


def test_the_counter_resets_so_the_next_tool_lasts_as_long() -> None:
    world = _Ground(tools=2)
    for _ in range(wear.USES_PER_TOOL):
        wear.wear_one_use(world, "a1", "harvest")
    assert world.tool_wear["a1"] == 0
    for _ in range(wear.USES_PER_TOOL - 1):
        assert wear.wear_one_use(world, "a1", "harvest") is False
    assert wear.wear_one_use(world, "a1", "harvest") is True
    assert world.inventory.quantity("a1", wear.TOOL_ITEM) == 0


def test_working_bare_handed_never_goes_negative() -> None:
    """A resident with no tool must not accrue a debt of tools. They
    simply work without one, which is allowed and more expensive."""
    world = _Ground(tools=0)
    for _ in range(wear.USES_PER_TOOL * 3):
        assert wear.wear_one_use(world, "a1", "harvest") is False
    assert world.inventory.quantity("a1", wear.TOOL_ITEM) == 0


def test_a_verb_that_is_not_work_does_not_advance_the_counter() -> None:
    world = _Ground(tools=1)
    for _ in range(wear.USES_PER_TOOL * 2):
        wear.wear_one_use(world, "a1", "move")
    assert world.tool_wear.get("a1", 0) == 0
    assert world.inventory.quantity("a1", wear.TOOL_ITEM) == 1


@pytest.mark.parametrize("verb", ["harvest", "mine", "fell_timber", "craft"])
def test_holding_a_tool_makes_the_work_cheaper(verb) -> None:
    """The demand side. Without it, owning a tool is pure cost and
    nobody would buy one."""
    bare = cost_for(verb, {"quantity": 5}, ActionActor("a1"))
    equipped = cost_for(verb, {"quantity": 5}, ActionActor("a1", holds_a_tool=True))
    assert equipped < bare, (
        f"GROW-23: {verb} costs the same with a tool as without, so an "
        "iron_tool is pure expense and nobody has a reason to buy one"
    )


def test_a_tool_does_not_discount_verbs_that_are_not_work() -> None:
    """Pricing a chisel into a journey would make the discount a general
    wealth transfer rather than a return on owning a tool."""
    for verb, params in (("move", {}), ("say", {"content": "hello"})):
        assert cost_for(verb, params, ActionActor("a1")) == cost_for(
            verb, params, ActionActor("a1", holds_a_tool=True)
        )


def test_the_floor_still_holds_for_a_skilled_resident_with_a_tool() -> None:
    """Two discounts through the floor would let the best-equipped
    residents act for free, and `min_cost` exists to stop exactly that."""
    from world.ledger.action_costs import DEFAULT_SCHEDULE

    rule = DEFAULT_SCHEDULE.rule_for("harvest")
    cheapest = cost_for(
        "harvest", {"quantity": 1},
        ActionActor("a1", skill_proficiency=100, holds_a_tool=True),
    )
    assert cheapest >= rule.min_cost > 0


def test_the_price_has_one_definition() -> None:
    """`wear.py` re-exports the schedule's constant rather than repeating
    it. Two copies would agree until the first time either moved."""
    from world.ledger.action_costs import TOOL_HELD_FEE_BPS

    assert wear.FEE_WITH_A_TOOL_BPS is TOOL_HELD_FEE_BPS


@pytest.mark.asyncio
async def test_real_work_through_the_registry_wears_the_tool(world, registry) -> None:
    """The mechanism existing is not the mechanism running. This goes
    through `registry.execute`, which is what a resident's turn calls."""
    await spawn_agent(world, "smith", "Kasukabe Forest")
    world.inventory.credit("smith", wear.TOOL_ITEM, 1)

    for _ in range(wear.USES_PER_TOOL):
        await registry.execute(world, "smith", "fell_timber", {"quantity": 1})

    assert world.inventory.quantity("smith", wear.TOOL_ITEM) == 0, (
        "GROW-23: a full tool's worth of work went through the real "
        "registry and consumed nothing - the mechanism has no caller"
    )


@pytest.mark.asyncio
async def test_a_failed_action_does_not_break_your_chisel(world, registry) -> None:
    """After the handler, so only a completed act counts - the same rule
    the proficiency award follows, for the same reason."""
    from world.tools.errors import ToolError

    await spawn_agent(world, "smith", "Hobbs Cafe")
    world.inventory.credit("smith", wear.TOOL_ITEM, 1)

    for _ in range(wear.USES_PER_TOOL):
        with pytest.raises(ToolError):
            await registry.execute(world, "smith", "fell_timber", {"quantity": 1})

    assert world.inventory.quantity("smith", wear.TOOL_ITEM) == 1
    assert world.tool_wear.get("smith", 0) == 0


def test_the_registry_resolves_whether_a_tool_is_held() -> None:
    """`holds_a_tool` defaults False and `action_costs` is pure, so a
    caller that never fills it is a discount nobody can earn."""
    import inspect

    from world.tools import registry as module

    source = inspect.getsource(module)
    assert "holds_a_tool=world.inventory.quantity" in source, (
        "GROW-23: nothing resolves holds_a_tool, so every resident is "
        "priced as bare-handed however many tools they own"
    )
