"""End-to-end: a farmer harvests, mills, bakes, and sells bread to a
grocer at a genesis-wired price, all through build_world()'s real
Ledger/CharterEngine/EventBus wiring - no primitive-family test double,
no mocked sibling module. Proves the seams in economy_bridge.py and
state.py actually close, not just that each family's own unit tests
pass in isolation.
"""

from __future__ import annotations

import pytest

from world.tools.bootstrap import build_world
from world.tools.locations import farmland_location, mill_location
from world.tools.models import Business
from world.tools.tests.conftest import in_conversation, action_cost, spawn_agent


@pytest.mark.asyncio
async def test_farm_to_table_chain_across_ledger_and_inventory() -> None:
    world, registry = await build_world(seed=7)

    await spawn_agent(world, "farmer", farmland_location("wheat"), starting_sim=1_000)
    await registry.execute(world, "farmer", "harvest", {"crop": "wheat", "quantity": 20})
    assert world.inventory.quantity("farmer", "wheat") == 20

    await registry.execute(world, "farmer", "move", {"destination_location_id": mill_location()})
    await registry.execute(world, "farmer", "craft", {"recipe_id": "craft:wheat->flour", "batches": 20})
    assert world.inventory.quantity("farmer", "flour") == 20
    assert world.inventory.quantity("farmer", "wheat") == 0

    await registry.execute(world, "farmer", "move", {"destination_location_id": "kitchen"})
    await registry.execute(world, "farmer", "cook", {"recipe_id": "cook:flour->bread", "batches": 20})
    assert world.inventory.quantity("farmer", "bread") == 20

    await spawn_agent(world, "grocer", "grocery-shop", starting_sim=1_000)
    await world.ledger.register_business("grocery-1", "grocer", fee_sim=100, tick=world.tick())
    await world.ledger.mint_sim("grocery-1", 10_000, tick=world.tick())
    world.businesses["grocery-1"] = Business(
        business_id="grocery-1", name="Corner Grocery", business_type="grocery",
        owner_agent_id="grocer", location_id="grocery-shop",
    )

    await registry.execute(world, "farmer", "move", {"destination_location_id": "grocery-shop"})
    await registry.execute(world, "grocer", "post_price", {
        "item": "bread", "bid_sim": 15, "on_behalf_of_business": "grocery-1",
    })
    in_conversation(world, "farmer", "grocer")
    await registry.execute(
        world, "farmer", "sell", {"buyer_id": "grocery-1", "item": "bread", "quantity": 20}
    )

    assert world.inventory.quantity("farmer", "bread") == 0
    assert world.inventory.quantity("grocery-1", "bread") == 20
    labour = (
        action_cost("harvest", {"crop": "wheat", "quantity": 20})
        + action_cost("move", {})
        + action_cost("craft", {"recipe_id": "craft:wheat->flour", "batches": 20})
        + action_cost("move", {})
        + action_cost("cook", {"recipe_id": "cook:flour->bread", "batches": 20})
        + action_cost("move", {})
        + action_cost("sell", {"buyer_id": "grocery-1", "item": "bread",
                               "quantity": 20, "total_price_sim": 20 * 15})
    )
    assert labour > 0, "acting must not be free"
    assert world.ledger.sim_balance("farmer") == 1_000 + 20 * 15 - labour

    assert world.skill_reps["farmer"] == {"farming": 1, "artisanry": 1, "cooking": 1}
    assert "farming" in world.skills["farmer"]


@pytest.mark.asyncio
async def test_sim_only_tool_calls_never_move_usd() -> None:
    """DESIGN.md section 3's $1000 USD invariant is ledger/'s law to
    enforce, but this proves tools/'s own primitives that only ever move
    sim-currency (marry's temple fee, pray's donation) really do stay
    sim-only end to end through the real Ledger - the USD total before
    and after must be bit-for-bit identical, not just "close."
    """
    world, registry = await build_world(seed=3)
    await spawn_agent(world, "a1", "temple", starting_sim=10_000)
    await spawn_agent(world, "a2", "temple", starting_sim=10_000)
    usd_before = world.ledger.usd_total()

    await registry.execute(world, "a1", "marry", {"spouse_agent_id": "a2", "new_household_id": "hh-x"})
    await registry.execute(world, "a1", "pray", {"donation_sim": 100})

    assert world.ledger.usd_total() == usd_before
