"""TOOL-29, then TOOL-06: `toolify`'s description and its price agree.

**This file did what it was written to do.** TOOL-29 found the blurb
promising "Each step still costs what it always did" while the wrapper's
own name fell through to `DEFAULT_RULE` and took 5 sim on top. That
agent could not remove the charge - it lives in the pricing half - so it
fixed the lie and left behind a guard that MEASURES the real cost rather
than checking the sentence against a constant, with a failure message
naming the fix that would make it red. TOOL-06 removed the charge, the
file turned red on exactly that message, and the words followed.

What is guarded now is the same property with the numbers moved: an
invented tool costs the sum of its steps and nothing more, and the
description says so, in the payloads a resident actually reads.

The original finding, kept because it is why any of this is here:

The blurb promised "Each step still costs what it always did" and stopped
there, which a resident reads as "a composite costs the sum of its steps".
It does not. `SCHEDULE_TABLE` cannot hold a row for a tool nobody has
invented yet, so the invented tool's OWN name falls through to
`DEFAULT_RULE` (5 sim, "never 0") and `dispatch` charges that on top of
every step the composite runs - `action_costs.py`'s own module docstring
says so in as many words, and the description contradicted it. Measured on
this checkout: two `look_around` calls made by hand cost 0 sim; the same
two wrapped in an invented tool and called once cost 5.

The fix TOOL-29 named as better - not charging the surcharge at all - is
what TOOL-06 did: `dispatch` skips the charge for any name in
`world.custom_tools`, rather than adding a zero row to a static table for
a tool a town can invent at any tick.
"""

from __future__ import annotations

import re

import pytest

from world.tools.registry import tool_registry_rows
from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"
STEPS = [
    {"order": 1, "tool_name": "look_around"},
    {"order": 2, "tool_name": "look_around"},
]


async def _inventor_with_a_composite(world, registry, agent_id: str = "n01") -> None:
    await spawn_agent(world, agent_id, FIELD)
    await registry.execute(world, agent_id, "toolify", {
        "name": "look_twice",
        "description": "look around, and look around again",
        "steps": STEPS,
    })


def _promises_no_surcharge(description: str) -> bool:
    """Whether the blurb tells a resident the wrapper is free.

    Prose rather than a number, because the number is now zero and
    "costs 0 sim more than its steps" is a sentence no resident should
    have to parse. The two things that must be true of the words are
    that they say the wrapper is free and that they do not still
    advertise a surcharge.
    """
    if re.search(r"costs \d+ sim more than its steps", description):
        return False
    if re.search(r"more than its steps", description):
        return False
    return "the wrapper itself is free" in description


@pytest.mark.asyncio
async def test_an_invented_tool_costs_exactly_its_steps(
    world_and_registry,
) -> None:
    """The finding, measured through `registry.execute` - the only path a
    resident ever calls anything by. Two `look_around` calls by hand
    against the same two inside an invented tool, called once."""
    world, registry = world_and_registry
    await _inventor_with_a_composite(world, registry)

    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "look_around", {})
    await registry.execute(world, "n01", "look_around", {})
    by_hand = before - world.ledger.sim_balance("n01")

    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "look_twice", {})
    as_a_tool = before - world.ledger.sim_balance("n01")

    assert as_a_tool == by_hand, (
        f"an invented tool cost {as_a_tool} sim where its two steps cost "
        f"{by_hand} by hand - the wrapper is being charged again (TOOL-06), "
        "and if that is deliberate the toolify description has to say so"
    )


@pytest.mark.asyncio
async def test_the_description_names_the_surcharge_it_charges(
    world_and_registry,
) -> None:
    """The whole finding: the words and the money have to agree.

    Measured, not compared against `DEFAULT_RULE`, so that fixing the
    pricing without fixing the sentence cannot pass.
    """
    world, registry = world_and_registry
    await _inventor_with_a_composite(world, registry)

    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "look_around", {})
    await registry.execute(world, "n01", "look_around", {})
    by_hand = before - world.ledger.sim_balance("n01")

    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "look_twice", {})
    as_a_tool = before - world.ledger.sim_balance("n01")

    surcharge = as_a_tool - by_hand
    if surcharge == 0:
        assert _promises_no_surcharge(registry.tools["toolify"].description), (
            "an invented tool costs exactly its steps and toolify still "
            f"advertises a surcharge: {registry.tools['toolify'].description!r}"
        )
    else:
        found = re.search(
            r"costs (\d+) sim more than its steps", registry.tools["toolify"].description
        )
        assert found and int(found.group(1)) == surcharge, (
            f"an invented tool costs {surcharge} sim over its steps and the "
            "description does not say so"
        )


@pytest.mark.asyncio
async def test_the_blurb_does_not_advertise_a_fee_that_is_gone(
    registry,
) -> None:
    """The sign-flipped version of the original defect.

    A description still announcing a 5-sim charge after the charge was
    removed misleads a resident exactly as badly as one that promised
    free steps while charging - it budgets for a fee it will never pay,
    and an invented tool looks more expensive than doing the work by
    hand, which is the opposite of what `toolify` is for.
    """
    assert _promises_no_surcharge(registry.tools["toolify"].description)


@pytest.mark.asyncio
async def test_the_honest_price_reaches_the_resident(world_and_registry) -> None:
    """WIRING. The description is only worth correcting where the resident
    reads it: the `GET /tools/registry` payload and `describe_primitives`.
    Since 2026-09-21 `toolify` is a one-line entry until the resident has
    authored something (X8), and an index row keeps only the first
    sentence - which would drop the price. So its line carries the price
    itself (`registry.INDEX_NOTES`), checked here rather than assumed.
    """
    world, registry = world_and_registry
    await spawn_agent(world, "n01", FIELD)

    rows = {row["name"]: row for row in tool_registry_rows(registry, world, "n01")}
    assert rows["toolify"].get("elsewhere"), "the one-line entry is the decision"
    assert "sum of its steps" in rows["toolify"]["description"], (
        "the one-line toolify dropped the price; the resident never learns it is free"
    )
    assert _promises_no_surcharge(rows["toolify"]["description"])

    outcome = await registry.execute(world, "n01", "describe_primitives", {})
    listed = {row["name"]: row for row in outcome.result["tools"]}
    assert _promises_no_surcharge(listed["toolify"]["description"])


@pytest.mark.asyncio
async def test_the_listing_quotes_what_the_call_will_actually_cost(
    world_and_registry,
) -> None:
    """PROG-02, and the third surface this same disagreement was found on.

    TOOL-29 found the BLURB claiming one thing while the charge did
    another. TOOL-06 removed the charge and left `action_costs`'
    disclosure row still describing it. And `describe_primitives` - the
    listing a resident reads before composing anything - quoted an
    invented tool at 5 sim, because an invented name has no row in the
    schedule and `cost_for` falls to `DEFAULT_RULE`, while `dispatch`
    charged nothing for the wrapper.

    So the town quoted a price it does not charge, in the one place a
    resident looks before deciding whether composing is worth it. Three
    readers of one fact, disagreeing, each fixed separately - which is
    why this measures the quote against the charge rather than against a
    constant.
    """
    world, registry = world_and_registry
    await _inventor_with_a_composite(world, registry)

    listing = await registry.execute(
        world, "n01", "describe_primitives", {"contains": "look_twice"}
    )
    rows = [row for row in listing.result["tools"] if row["name"] == "look_twice"]
    assert rows, "the invented tool is not in its own inventor's listing"
    quoted = rows[0]["sim_cost_minimum"]

    before = world.ledger.sim_balance("n01")
    await registry.execute(world, "n01", "look_twice", {})
    charged = before - world.ledger.sim_balance("n01")

    assert quoted == charged, (
        f"the listing quotes {quoted} sim for `look_twice` and the call "
        f"costs {charged}. A resident deciding whether to compose reads the "
        "quote, so a wrong one argues against a feature the town has."
    )
