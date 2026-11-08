"""Tools wear out, so making them is worth something.

`GROW-23`. `recipes.py` outputs `planks`, `furniture`, `cloth`,
`{metal}_ingot`, `iron_tool`, `copper_wire`, `tin_ware`,
`silver_jewelry`, `gold_jewelry`. Exactly one of those chains has a
consumer: crops become food and `eat` consumes it. Everything else piles
up for ever.

"Tool wear" existed only as a sim-currency fee in `action_costs.py` - a
number subtracted from a wallet, with no item behind it. So the
artisanry proficiency Silas, Junia and Tobias built up over run 23
converted into nothing the town needed: they could make an `iron_tool`
and nobody, including them, had any reason to want one.

This gives `iron_tool` a sink. A resident doing manual work wears one
out, and while they hold one the wear FEE is lower - so owning a tool is
worth paying for, and a smith has customers.

WHAT THIS IS NOT. The review's full fix also makes buildings consume
planks and ingots and adds building maintenance with a `repair_site`
verb. The buildings half belongs to GROW-08, which owns the duplicate
bills of materials, and doing it here would put two different
constructions of the same bill in the tree - which is the defect GROW-08
is about. `furniture`, `cloth`, `copper_wire`, `tin_ware` and the two
jewelleries still have no sink; that is stated plainly rather than
half-solved.
"""
from __future__ import annotations

TOOL_ITEM = "iron_tool"

WEARING_TOOLS = frozenset({"harvest", "mine", "fell_timber", "craft", "cook", "work_site"})

USES_PER_TOOL = 40

from world.ledger.action_costs import TOOL_HELD_FEE_BPS as FEE_WITH_A_TOOL_BPS  # noqa: E402


def wears_a_tool(tool_name: str) -> bool:
    return tool_name in WEARING_TOOLS


def wear_one_use(world, agent_id: str, tool_name: str) -> bool:
    """Record one act of manual work; consume a tool if it is worn out.

    Returns True when a tool was actually consumed, so a caller can say
    so. Never raises and never goes negative: a resident working
    bare-handed simply accrues uses against a tool they do not have, and
    the counter is spent the moment they buy one - which is the honest
    reading of "these hands have done a lot of work".
    """
    if not wears_a_tool(tool_name):
        return False
    used = world.tool_wear.get(agent_id, 0) + 1
    if used < USES_PER_TOOL:
        world.tool_wear[agent_id] = used
        return False

    world.tool_wear[agent_id] = 0
    if world.inventory.quantity(agent_id, TOOL_ITEM) <= 0:
        return False
    world.inventory.debit(agent_id, TOOL_ITEM, 1)
    return True
