"""TOOL-03/TOOL-15: a business nobody could stock, and a till nobody could open.

Stock lives under the business_id in `world.inventory`; money in a ledger
account of the same name. Neither had a door.

`craft` and `cook` put goods in the AGENT's hands. Nothing moved them to the
shop - `store_item` builds a `Storage`, `transfer` and `give` are
agent-to-agent. Nothing moved sim out of the till either: `pay_wage` pays
only `employee_agent_ids`, `transfer` spends the actor's own account.

So Marta baked bread into her own hands and could not shelve it, while
seventeen people stood in her shop and `buy` answered from the twelve loaves
genesis happened to put there. The only route the physics allowed was to
hire yourself and pay yourself - which worked because `hire_employee` never
checked the employee was somebody else. TOOL-15, closed here.

The last test is the block's gate: bake, shelve, price, a stranger buys, the
owner draws the takings - all of it through `registry.execute`.
"""

from __future__ import annotations

import pytest

from world.ledger.models import TransactionReason
from world.tools.errors import (
    ActorRequirementError,
    InsufficientInventoryError,
    LocationGateError,
    ToolValidationError,
)
from world.tools.models import Business
from world.tools.state import WorldState

from .conftest import in_conversation, spawn_agent

BAKERY_AT = "Kasukabe Town Square"


async def _bakery(world: WorldState, *, owner: str = "n07", staff: str | None = None):
    await spawn_agent(world, owner, BAKERY_AT)
    world.agent_names[owner] = "Marta Thorne"
    employees = {staff} if staff else set()
    business = Business(
        business_id="b_bakery",
        name="Thorne's Bakery",
        business_type="bakery",
        owner_agent_id=owner,
        employee_agent_ids=employees,
        location_id=BAKERY_AT,
        provides_workshops={"kitchen"},
    )
    world.businesses["b_bakery"] = business
    await world.ledger.register_business("b_bakery", owner, fee_sim=1, tick=world.tick())
    if staff:
        await spawn_agent(world, staff, BAKERY_AT)
    return business


@pytest.mark.asyncio
async def test_the_baker_can_put_her_bread_on_her_own_shelf(world, registry):
    await _bakery(world)
    world.inventory.credit("n07", "bread", 6)

    outcome = await registry.execute(
        world, "n07", "stock_shelf", {"business_id": "b_bakery", "item": "bread", "quantity": 4}
    )

    assert world.inventory.quantity("b_bakery", "bread") == 4
    assert world.inventory.quantity("n07", "bread") == 2
    assert outcome.events[0].type == "business.stock.moved"


@pytest.mark.asyncio
async def test_stock_can_come_back_off_the_shelf(world, registry):
    await _bakery(world)
    world.inventory.credit("b_bakery", "bread", 5)

    await registry.execute(
        world,
        "n07",
        "take_from_shelf",
        {"business_id": "b_bakery", "item": "bread", "quantity": 2},
    )

    assert world.inventory.quantity("b_bakery", "bread") == 3
    assert world.inventory.quantity("n07", "bread") == 2


@pytest.mark.asyncio
async def test_you_cannot_shelve_what_you_are_not_holding(world, registry):
    await _bakery(world)
    world.inventory.credit("n07", "bread", 1)

    with pytest.raises(InsufficientInventoryError):
        await registry.execute(
            world,
            "n07",
            "stock_shelf",
            {"business_id": "b_bakery", "item": "bread", "quantity": 9},
        )


@pytest.mark.asyncio
async def test_a_stranger_cannot_stock_or_rob_the_shelf(world, registry):
    await _bakery(world)
    await spawn_agent(world, "n02", BAKERY_AT)
    world.inventory.credit("b_bakery", "bread", 5)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "n02",
            "take_from_shelf",
            {"business_id": "b_bakery", "item": "bread", "quantity": 1},
        )


@pytest.mark.asyncio
async def test_the_shelf_is_out_of_reach_from_across_town(world, registry):
    await _bakery(world)
    world.inventory.credit("n07", "bread", 5)
    await spawn_agent(world, "n99", "Kasukabe Farmland")
    world.businesses["b_bakery"].employee_agent_ids.add("n99")
    world.inventory.credit("n99", "bread", 5)

    with pytest.raises(LocationGateError):
        await registry.execute(
            world,
            "n99",
            "stock_shelf",
            {"business_id": "b_bakery", "item": "bread", "quantity": 1},
        )


@pytest.mark.asyncio
async def test_the_owner_can_draw_the_takings(world, registry):
    await _bakery(world)
    await world.ledger.mint_sim("b_bakery", 500, tick=world.tick())
    before = world.ledger.sim_balance("n07")

    await registry.execute(
        world, "n07", "draw_from_till", {"business_id": "b_bakery", "amount_sim": 300}
    )

    assert world.ledger.sim_balance("b_bakery") == 200
    assert world.ledger.sim_balance("n07") > before


@pytest.mark.asyncio
async def test_a_till_movement_is_not_filed_as_a_gift(world, registry):
    """The trace has to tell banking the takings from giving money away."""
    await _bakery(world)
    await world.ledger.mint_sim("b_bakery", 500, tick=world.tick())

    await registry.execute(
        world, "n07", "draw_from_till", {"business_id": "b_bakery", "amount_sim": 100}
    )

    reasons = [t.reason for t in world.ledger.transactions]
    assert TransactionReason.TILL in reasons


@pytest.mark.asyncio
async def test_staff_may_put_money_in_and_may_not_take_it_out(world, registry):
    """The one place employee and owner have to differ.

    Stocking, unstocking and banking are things an employee does in the
    course of the job. Taking money OUT is not - otherwise a wage is
    pointless and `steal` has a legal synonym.
    """
    await _bakery(world, staff="n03")

    await registry.execute(
        world, "n03", "deposit_to_till", {"business_id": "b_bakery", "amount_sim": 50}
    )
    assert world.ledger.sim_balance("b_bakery") >= 50

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "n03", "draw_from_till", {"business_id": "b_bakery", "amount_sim": 10}
        )


@pytest.mark.asyncio
async def test_a_till_cannot_be_overdrawn(world, registry):
    await _bakery(world)
    await world.ledger.mint_sim("b_bakery", 40, tick=world.tick())

    with pytest.raises(ToolValidationError):
        await registry.execute(
            world, "n07", "draw_from_till", {"business_id": "b_bakery", "amount_sim": 900}
        )


@pytest.mark.asyncio
async def test_the_till_answers_to_the_prefixed_id_too(world, registry):
    """D1's fold reaches the new verbs, because they went through it."""
    await _bakery(world)
    await world.ledger.mint_sim("b_bakery", 200, tick=world.tick())

    await registry.execute(
        world,
        "n07",
        "draw_from_till",
        {"business_id": "business:b_bakery", "amount_sim": 100},
    )
    assert world.ledger.sim_balance("b_bakery") == 100


@pytest.mark.asyncio
async def test_an_owner_cannot_hire_herself(world, registry):
    """Until `draw_from_till` existed this was the only way to open a till."""
    await _bakery(world)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "n07",
            "hire_employee",
            {"business_id": "b_bakery", "employee_agent_id": "n07", "employee_consent": True},
        )
    assert "n07" not in world.businesses["b_bakery"].employee_agent_ids


@pytest.mark.asyncio
async def test_bake_shelve_price_sell_and_bank_it(world, registry):
    """One trace: a resident stocks a shelf and another buys from it.

    This is the loop that was broken end to end in run 23 - `cook` refused
    2 of 2, `post_price` refused on the prefixed id, `buy` succeeded once
    in nine - and every step of it goes through `registry.execute`.
    """
    await _bakery(world)
    await spawn_agent(world, "n02", BAKERY_AT)
    world.inventory.credit("n07", "flour", 6)

    await registry.execute(
        world, "n07", "cook", {"recipe_id": "cook:flour->bread", "batches": 2}
    )
    baked = world.inventory.quantity("n07", "bread")
    assert baked > 0

    await registry.execute(
        world,
        "n07",
        "stock_shelf",
        {"business_id": "b_bakery", "item": "bread", "quantity": baked},
    )

    await registry.execute(
        world,
        "n07",
        "post_price",
        {"item": "bread", "ask_sim": 20, "on_behalf_of_business": "business:b_bakery"},
    )

    in_conversation(world, "n02", "n07")
    await registry.execute(
        world,
        "n02",
        "buy",
        {"seller_id": "business:b_bakery", "item": "bread", "quantity": 1},
    )
    assert world.inventory.quantity("n02", "bread") == 1

    till = world.ledger.sim_balance("b_bakery")
    assert till >= 20
    await registry.execute(
        world, "n07", "draw_from_till", {"business_id": "b_bakery", "amount_sim": 20}
    )
    assert world.ledger.sim_balance("b_bakery") == till - 20
