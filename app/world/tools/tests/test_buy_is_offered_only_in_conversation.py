"""`buy` is offered to a resident only while it is in a conversation.

The operator's rule, 2026-09-21: "The buy tool will only be enabled after the
participants of the buy process exist in a conversation. Single or group
conversations are equally applicable."

b028330 made a purchase REFUSE unless buyer and seller were talking. That
still left `buy` in every resident's tool list at all times, so a resident
with nobody to talk to could spend a paid call on it and be told no. Now the
tool is not OFFERED outside a conversation at all: it appears in the list a
resident's engine fetches (`GET /tools/registry?agent_id=...`) only while the
resident is on the roster of an open conversation - one-to-one or a group,
equally - with at least one other member standing there too.

Two things stay as they were, on purpose:

  * the refusal is the backstop. A call that reaches `buy` anyway - a saved
    program's step, an engine holding an older list - still gets the precise
    reason ("you and X are not in a conversation"), not a bare "no such
    tool", which is what hiding it in `visible_to` would have produced;
  * `sell` and `trade` are still offered and still refused at call time.
    The instruction named `buy`.
"""

from __future__ import annotations

import pytest

from world.tools.models import PricePost
from world.tools.registry import tool_registry_rows
from world.tools.tests.conftest import in_conversation, spawn_agent

MARKET = "market_square"


def _offered(registry, world, agent_id) -> set[str]:
    return {row["name"] for row in tool_registry_rows(registry, world, agent_id)}


async def _two_residents(world):
    await spawn_agent(world, "buyer", MARKET)
    await spawn_agent(world, "seller", MARKET)
    world.ledger.get_account("buyer").sim_units = 5_000
    world.price_board["seller"] = {"potato": PricePost(item="potato", ask_sim=8)}
    world.inventory.credit("seller", "potato", 5)


@pytest.mark.asyncio
async def test_a_resident_talking_to_nobody_is_not_offered_buy(world, registry):
    await _two_residents(world)

    assert "buy" not in _offered(registry, world, "buyer")


@pytest.mark.asyncio
async def test_a_one_to_one_conversation_offers_it(world, registry):
    await _two_residents(world)
    in_conversation(world, "buyer", "seller")

    assert "buy" in _offered(registry, world, "buyer")


@pytest.mark.asyncio
async def test_a_group_conversation_offers_it_equally(world, registry):
    """Single or group, the operator's words: a table of three counts."""
    await _two_residents(world)
    await spawn_agent(world, "neighbour", MARKET)
    in_conversation(world, "neighbour", "buyer", "seller")

    assert "buy" in _offered(registry, world, "buyer")
    assert "buy" in _offered(registry, world, "neighbour")


@pytest.mark.asyncio
async def test_speaking_to_someone_is_what_turns_it_on(world, registry):
    """The path a resident takes, with no fixture opening anything for it."""
    await _two_residents(world)
    assert "buy" not in _offered(registry, world, "buyer")

    await registry.execute(world, "buyer", "say",
                           {"content": "Good morning.", "listener_agent_id": "seller"})

    assert "buy" in _offered(registry, world, "buyer")


@pytest.mark.asyncio
async def test_a_remark_to_the_room_is_not_a_conversation_with_anybody(world, registry):
    """Speaking to nobody in particular opens a thread of one."""
    await _two_residents(world)

    await registry.execute(world, "buyer", "say", {"content": "Lovely morning."})

    assert "buy" not in _offered(registry, world, "buyer")


@pytest.mark.asyncio
async def test_it_goes_away_when_the_conversation_goes_quiet(world, registry):
    from world.tools.state import CONVERSATION_WINDOW_TICKS

    await _two_residents(world)
    in_conversation(world, "buyer", "seller")
    world.current_tick += CONVERSATION_WINDOW_TICKS + 1

    assert "buy" not in _offered(registry, world, "buyer")


@pytest.mark.asyncio
async def test_it_goes_away_when_the_other_person_walks_off(world, registry):
    await _two_residents(world)
    in_conversation(world, "buyer", "seller")
    world.locations.place("seller", "street")

    assert "buy" not in _offered(registry, world, "buyer")


@pytest.mark.asyncio
async def test_sell_and_trade_are_still_offered(world, registry):
    """The instruction named `buy`. The other two keep their call-time
    refusal (`commerce._require_dealing`)."""
    await _two_residents(world)

    offered = _offered(registry, world, "buyer")

    assert "sell" in offered and "trade" in offered


@pytest.mark.asyncio
async def test_a_call_that_slips_through_is_refused_with_the_reason(world, registry):
    """Not "no such tool": a saved program or an engine holding an older list
    may still reach `buy`, and a resident told why can do something about it."""
    await _two_residents(world)

    with pytest.raises(Exception) as refused:
        await registry.execute(world, "buyer", "buy",
                               {"seller_id": "seller", "item": "potato", "quantity": 1})

    assert "conversation" in str(refused.value).lower()


@pytest.mark.asyncio
async def test_the_list_for_nobody_in_particular_still_has_it(world, registry):
    """The unfiltered payload - the standalone server's boot, the observatory,
    the tests - names every tool, exactly as before."""
    await _two_residents(world)

    assert "buy" in {row["name"] for row in tool_registry_rows(registry)}


@pytest.mark.asyncio
async def test_switching_the_rule_off_offers_it_always(world, registry, monkeypatch):
    import world.tools.primitives.commerce as commerce

    monkeypatch.setattr(commerce, "SALES_NEED_CONVERSATION", False)
    await _two_residents(world)

    assert "buy" in _offered(registry, world, "buyer")
