"""Two people standing together can swap goods for money, atomically.

DESIGN.md section 2 lists `trade` among the primitives. ROADMAP.md T7 lists it
as still missing: "*Missing primitives.* `trade`, an atomic two-sided swap
distinct from `buy` and `sell` against a posted price." This is that.

MEASURED, iteration 3 of the autonomous loop. Six residents held a real market
negotiation at `home:h_field`:

    Tobias -> Marta:  "Ten units of fresh wheat. You need flour for the oven?"
    Marta  -> Tobias: "45 per unit is fair for fresh cut."

A price was agreed out loud, and then nothing happened, because nothing COULD.
`sell` is gated to a business's own location, `buy` likewise, and `transfer`
moves sim but no goods. Two residents standing in a kitchen with wheat and
money between them had no way to complete the exchange they had just agreed.
Zero settlements in the run, and not because anybody refused.

WHY ATOMIC MATTERS. Split into "you transfer, then I hand over", either half
can be the last thing that happens - and the loser has no recourse in a town
whose justice system cannot see intent. An atomic swap makes the bargain the
unit, so a trade either happens or does not, and neither party can be left
holding half of one. That is a physics decision, not a fairness one: it
removes a failure mode rather than adjudicating it.

The research question this deliberately leaves open is whether agents trade
FAIRLY, not whether they can be cheated by a race in the ledger.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.errors import LocationGateError, ToolValidationError
from world.tools.tests.conftest import action_cost, in_conversation, move_agent, spawn_agent


@pytest_asyncio.fixture
async def trade_world(world, registry):
    """Two residents standing in the same kitchen, which is the situation
    every negotiation in the run actually happened in."""
    await spawn_agent(world, "seller", "home:h_field")
    await spawn_agent(world, "buyer", "home:h_field")
    in_conversation(world, "seller", "buyer")
    return world, registry, "seller", "buyer"


@pytest.mark.asyncio
async def test_two_co_located_agents_swap_goods_for_money(trade_world) -> None:
    world, registry, a, b = trade_world
    world.inventory.credit(a, "wheat", 10)
    before_a = world.ledger.sim_balance(a)
    before_b = world.ledger.sim_balance(b)
    await registry.execute(
        world, a, "trade",
        {"counterparty_agent_id": b, "item": "wheat", "quantity": 10, "price_sim": 450},
    )
    assert world.inventory.quantity(a, "wheat") == 0
    assert world.inventory.quantity(b, "wheat") == 10
    assert world.ledger.sim_balance(a) == before_a + 450 - action_cost(
        "trade", {"counterparty_agent_id": b, "item": "wheat", "quantity": 10, "price_sim": 450}
    )
    assert world.ledger.sim_balance(b) == before_b - 450


@pytest.mark.asyncio
async def test_a_trade_needs_both_parties_in_the_same_place(trade_world) -> None:
    world, registry, a, b = trade_world
    world.inventory.credit(a, "wheat", 10)
    await move_agent(world, b, "forest")
    with pytest.raises((LocationGateError, ToolValidationError)):
        await registry.execute(
            world, a, "trade",
            {"counterparty_agent_id": b, "item": "wheat", "quantity": 1, "price_sim": 10},
        )


@pytest.mark.asyncio
async def test_a_seller_without_the_goods_moves_no_money(trade_world) -> None:
    """The half-completed trade this primitive exists to prevent."""
    world, registry, a, b = trade_world
    before_b = world.ledger.sim_balance(b)
    with pytest.raises(Exception):
        await registry.execute(
            world, a, "trade",
            {"counterparty_agent_id": b, "item": "wheat", "quantity": 5, "price_sim": 50},
        )
    assert world.ledger.sim_balance(b) == before_b
    assert world.inventory.quantity(b, "wheat") == 0


@pytest.mark.asyncio
async def test_a_buyer_who_cannot_pay_keeps_the_seller_s_goods_where_they_were(trade_world) -> None:
    world, registry, a, b = trade_world
    world.inventory.credit(a, "wheat", 10)
    huge = world.ledger.sim_balance(b) + 1_000_000
    with pytest.raises(Exception):
        await registry.execute(
            world, a, "trade",
            {"counterparty_agent_id": b, "item": "wheat", "quantity": 10, "price_sim": huge},
        )
    assert world.inventory.quantity(a, "wheat") == 10
    assert world.inventory.quantity(b, "wheat") == 0


@pytest.mark.asyncio
async def test_you_cannot_trade_with_yourself(trade_world) -> None:
    world, registry, a, _ = trade_world
    world.inventory.credit(a, "wheat", 5)
    with pytest.raises(Exception):
        await registry.execute(
            world, a, "trade",
            {"counterparty_agent_id": a, "item": "wheat", "quantity": 1, "price_sim": 1},
        )


@pytest.mark.asyncio
async def test_a_gift_is_a_trade_at_zero(trade_world) -> None:
    """Charity is a price, not a separate mechanic. DESIGN.md's research
    question is whether agents give things away; a tool that forbade it
    would answer that question for them."""
    world, registry, a, b = trade_world
    world.inventory.credit(a, "bread", 2)
    await registry.execute(
        world, a, "trade",
        {"counterparty_agent_id": b, "item": "bread", "quantity": 2, "price_sim": 0},
    )
    assert world.inventory.quantity(b, "bread") == 2


@pytest.mark.asyncio
async def test_a_completed_trade_is_visible_to_both_parties(trade_world) -> None:
    """Both sides must perceive it, or a settlement cannot be reconstructed
    from the trace and neither party can act on it next turn."""
    world, registry, a, b = trade_world
    world.inventory.credit(a, "wheat", 3)
    result = await registry.execute(
        world, a, "trade",
        {"counterparty_agent_id": b, "item": "wheat", "quantity": 3, "price_sim": 30},
    )
    involved = {
        agent
        for event in result.events
        for agent in (event.payload.get("involved_agent_ids") or [])
    }
    assert {a, b} <= involved
