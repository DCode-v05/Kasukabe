"""The audit's own example, run: one decision, the whole morning's work.

`docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 7 makes one concrete
claim and this file is it, end to end:

> If a resident could write a small program - *walk to the mill, take 5
> flour, walk to the shop, stock the shelf, post 30* - that is one model
> call for six acts instead of six calls, and the ratio the project
> measures changes shape.

Every other guard in this directory holds one property of the mechanism:
the ceilings bind, a condition decides, a binding resolves, a plain
composite is unchanged. None of them shows the thing working. This does,
in the sequence the audit wrote, against the real registry, ending in a
shelf a stranger can buy from - because a program that files, validates
and dispatches correctly and still leaves nothing to buy would satisfy
every other test here and be worth nothing.

It is also the shape of the morning that failed in run
`deni-a6f9eb4-5h`. `test_the_town_can_actually_sell_something` replays
that as five hand-made calls after TRADE-01/07. This replays it as one.
"""

from __future__ import annotations

import pytest

from world.tools.models import Business
from world.tools.tests.conftest import in_conversation, spawn_agent

MILL = "b_mill"
SHOP = "b_grocery"
WHERE = "Kasukabe Farmland"


async def _a_mill_and_a_shop(world, owner: str) -> None:
    """`build_world` seeds no businesses - genesis does - so a test about
    a morning's trade has to open the two premises it moves between."""
    await spawn_agent(world, owner, WHERE)
    for business_id, name, kind in (
        (MILL, "Vance Mill", "mill"),
        (SHOP, "Kasukabe Grocery", "grocery"),
    ):
        world.businesses[business_id] = Business(
            business_id=business_id, name=name, business_type=kind,
            owner_agent_id=owner, location_id=WHERE,
        )
        await world.ledger.register_business(business_id, owner, fee_sim=1, tick=world.tick())
        await world.ledger.mint_sim(business_id, 5_000, tick=world.tick())


@pytest.mark.asyncio
async def test_one_call_walks_takes_carries_stocks_and_prices(world, registry):
    """Six acts, one decision, and a shelf somebody else can buy from.

    The assertion that matters is the last one. A program that ran five
    steps and left the ask on the person rather than the shelf would pass
    a count of dispatches and settle nothing - which is exactly what run
    `deni-a6f9eb4-5h` did four times, by hand.
    """
    await _a_mill_and_a_shop(world, "n11")
    world.inventory.credit(MILL, "flour", 12)

    await registry.execute(
        world,
        "n11",
        "toolify",
        {
            "name": "carry_flour_to_the_shop",
            "description": "the morning: fetch flour from the mill and price it in the shop",
            "param_schema": {"how_many": "int", "ask": "int"},
            "steps": [
                {"tool_name": "move", "params": {"destination_location_id": f"business:{MILL}"}},
                {
                    "tool_name": "take_from_shelf",
                    "params": {"business_id": MILL, "item": "flour", "quantity": "$how_many"},
                    "bind": "fetched",
                },
                {"tool_name": "move", "params": {"destination_location_id": f"business:{SHOP}"}},
                {
                    "tool_name": "stock_shelf",
                    "params": {"business_id": SHOP, "item": "flour", "quantity": "$how_many"},
                    "when": {"left": "@fetched.quantity", "op": ">", "right": 0},
                },
                {
                    "tool_name": "post_price",
                    "params": {
                        "item": "flour",
                        "ask_sim": "$ask",
                        "on_behalf_of_business": SHOP,
                    },
                    "when": {"left": "@fetched.quantity", "op": ">", "right": 0},
                },
            ],
        },
    )

    world.tool_invocations.clear()
    outcome = await registry.execute(
        world, "n11", "carry_flour_to_the_shop", {"how_many": 5, "ask": 30}
    )

    wrappers = [r for r in world.tool_invocations if r.program_step == 0]
    assert len(wrappers) == 1

    acts = [r for r in world.tool_invocations if r.program_step]
    assert len(acts) == 5, [r.tool_name for r in acts]
    assert len({r.program_id for r in world.tool_invocations}) == 1
    assert outcome.result["steps_skipped"] == 0

    assert world.inventory.quantity(SHOP, "flour") == 5
    posted = world.posted(SHOP, "flour")
    assert posted is not None, (
        "the program ran every step and priced nothing the shop holds - "
        "which is run deni-a6f9eb4-5h's morning with extra steps"
    )
    assert posted.ask_sim == 30


@pytest.mark.asyncio
async def test_a_customer_who_speaks_to_the_shop_can_buy_what_the_program_priced(world, registry):
    """The far end of the wire, since that is where this project's
    failures live. A price posted where nobody can complete it looks
    identical to a price posted where they can, from the seller's side."""
    await _a_mill_and_a_shop(world, "n11")
    world.inventory.credit(MILL, "flour", 12)
    await registry.execute(
        world,
        "n11",
        "toolify",
        {
            "name": "shop_run",
            "description": "fetch and price",
            "param_schema": {"how_many": "int"},
            "steps": [
                {
                    "tool_name": "take_from_shelf",
                    "params": {"business_id": MILL, "item": "flour", "quantity": "$how_many"},
                },
                {
                    "tool_name": "stock_shelf",
                    "params": {"business_id": SHOP, "item": "flour", "quantity": "$how_many"},
                },
                {
                    "tool_name": "post_price",
                    "params": {"item": "flour", "ask_sim": 16, "on_behalf_of_business": SHOP},
                },
            ],
        },
    )
    await registry.execute(world, "n11", "shop_run", {"how_many": 4})

    await spawn_agent(world, "n17", WHERE, starting_sim=500)
    in_conversation(world, "n17", "n11")
    bought = await registry.execute(
        world, "n17", "buy", {"item": "flour", "quantity": 2, "seller_id": SHOP}
    )

    assert bought.result["quantity"] == 2
    assert world.inventory.quantity("n17", "flour") == 2


@pytest.mark.asyncio
async def test_the_guard_skips_the_steps_that_depended_on_a_short_fetch(
    world, registry
):
    """The condition earning its place, and one level deep.

    TRADE-03's dead listing is an ask posted over goods the seller does
    not have, discovered only when somebody else is refused. A fixed list
    posts it every morning; a guarded step does not. Here the mill gives
    back less than the program was written for, and the two steps that
    depended on the full fetch are skipped rather than run over nothing.

    `@fetched.quantity` is deliberately one level. Two is refused at
    filing (`test_a_program_that_cannot_run_is_refused_at_filing`), so a
    guard reaching into a nested field is a program that never files -
    which is worth knowing while writing one of these, and is why this
    reads a top-level field of the step result.
    """
    await _a_mill_and_a_shop(world, "n11")
    world.inventory.credit(MILL, "flour", 2)
    await registry.execute(
        world,
        "n11",
        "toolify",
        {
            "name": "careful_shop_run",
            "description": "fetch, and price only a full load",
            "param_schema": {"how_many": "int", "enough": "int"},
            "steps": [
                {
                    "tool_name": "take_from_shelf",
                    "params": {"business_id": MILL, "item": "flour", "quantity": "$how_many"},
                    "bind": "fetched",
                },
                {
                    "tool_name": "stock_shelf",
                    "params": {"business_id": SHOP, "item": "flour", "quantity": "$how_many"},
                    "when": {"left": "@fetched.quantity", "op": ">=", "right": "$enough"},
                },
                {
                    "tool_name": "post_price",
                    "params": {"item": "flour", "ask_sim": 16, "on_behalf_of_business": SHOP},
                    "when": {"left": "@fetched.quantity", "op": ">=", "right": "$enough"},
                },
            ],
        },
    )

    outcome = await registry.execute(
        world, "n11", "careful_shop_run", {"how_many": 2, "enough": 5}
    )

    assert outcome.result["steps_skipped"] == 2, outcome.result
    assert world.posted(SHOP, "flour") is None, (
        "an ask was posted over stock the shop does not hold; the guard "
        "did not guard"
    )
    world.inventory.credit(MILL, "flour", 8)
    outcome = await registry.execute(
        world, "n11", "careful_shop_run", {"how_many": 5, "enough": 5}
    )
    assert outcome.result["steps_skipped"] == 0
    assert world.posted(SHOP, "flour") is not None
