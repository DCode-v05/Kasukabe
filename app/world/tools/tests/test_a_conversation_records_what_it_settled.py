"""A2A: a conversation resolves into what it settled.

docs/A2A_CONFORMANCE.md §2, "a session's outcome": a conversation closed and
nothing recorded what it had settled - the price agreed, the trade it led
to. Since a sale needs a conversation (b028330) the world already knew which
conversation every deal was agreed in; it just did not write it down. Now:

  * every completed `buy`, `sell` and `trade` is filed under the
    conversation it was agreed in, and its event carries `conversation_id`;
  * a conversation that closes gets an `outcome` - how many deals, how much
    sim moved, which goods - and 0 deals is an answer too (N6);
  * a refused deal files nothing, and the world's own actors, who need no
    conversation, file nothing and break nothing.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from world.tools.models import PricePost
from world.tools.state import CONVERSATION_WINDOW_TICKS
from world.tools.tests.conftest import in_conversation, spawn_agent

MARKET = "market_square"


async def _market(world):
    await spawn_agent(world, "buyer", MARKET)
    await spawn_agent(world, "seller", MARKET)
    world.ledger.get_account("buyer").sim_units = 5_000
    world.price_board["seller"] = {"potato": PricePost(item="potato", ask_sim=8, bid_sim=6)}
    world.inventory.credit("seller", "potato", 5)


@pytest.mark.asyncio
async def test_a_purchase_is_filed_under_its_conversation(world, registry):
    await _market(world)
    conversation_id = in_conversation(world, "buyer", "seller")

    outcome = await registry.execute(world, "buyer", "buy",
                                     {"seller_id": "seller", "item": "potato", "quantity": 2})

    deals = world.conversations[conversation_id]["deals"]
    assert len(deals) == 1
    assert deals[0] | {"tick": 0} == {"kind": "buy", "seller_id": "seller", "buyer_id": "buyer",
                                      "item": "potato", "quantity": 2, "total_sim": 16, "tick": 0}
    event = next(e for e in outcome.events if e.type == "business.sale.completed")
    assert event.payload["conversation_id"] == conversation_id


@pytest.mark.asyncio
async def test_a_trade_is_filed_with_its_agreed_price(world, registry):
    await _market(world)
    conversation_id = in_conversation(world, "seller", "buyer")

    outcome = await registry.execute(world, "seller", "trade", {
        "counterparty_agent_id": "buyer", "item": "potato", "quantity": 3, "price_sim": 21})

    [deal] = world.conversations[conversation_id]["deals"]
    assert (deal["kind"], deal["quantity"], deal["total_sim"]) == ("trade", 3, 21)
    event = next(e for e in outcome.events if e.type == "economy.trade.settled")
    assert event.payload["conversation_id"] == conversation_id


@pytest.mark.asyncio
async def test_a_sale_to_a_posted_bid_is_filed_too(world, registry):
    await _market(world)
    world.price_board["buyer"] = {"potato": PricePost(item="potato", bid_sim=6)}
    world.ledger.get_account("buyer").sim_units = 5_000
    conversation_id = in_conversation(world, "seller", "buyer")

    await registry.execute(world, "seller", "sell", {"buyer_id": "buyer", "item": "potato", "quantity": 1})

    [deal] = world.conversations[conversation_id]["deals"]
    assert (deal["kind"], deal["seller_id"], deal["buyer_id"], deal["total_sim"]) == ("sell", "seller", "buyer", 6)


@pytest.mark.asyncio
async def test_a_refused_deal_files_nothing(world, registry):
    await _market(world)
    conversation_id = in_conversation(world, "buyer", "seller")

    with pytest.raises(Exception):
        await registry.execute(world, "buyer", "buy",
                               {"seller_id": "seller", "item": "potato", "quantity": 50})

    assert world.conversations[conversation_id]["deals"] == []


@pytest.mark.asyncio
async def test_a_closed_conversation_says_what_it_settled(world, registry):
    await _market(world)
    conversation_id = in_conversation(world, "buyer", "seller")
    await registry.execute(world, "buyer", "buy", {"seller_id": "seller", "item": "potato", "quantity": 2})
    quiet = in_conversation(world, "buyer")

    closed = world.close_stale_conversations(world.tick() + CONVERSATION_WINDOW_TICKS + 1)

    assert conversation_id in closed and quiet in closed
    assert world.conversations[conversation_id]["outcome"] == {"deals": 1, "sim_moved": 16, "items": ["potato"]}
    assert world.conversations[quiet]["outcome"] == {"deals": 0, "sim_moved": 0, "items": []}
    assert world.conversations[conversation_id]["closed_tick"] > 0


def test_the_report_reads_what_each_conversation_settled(tmp_path) -> None:
    from world.run_report import settled_by_conversation

    db = tmp_path / "trace.db"
    conn = sqlite3.connect(db)
    conn.execute("create table events (type text, payload text, tick integer)")
    conn.executemany("insert into events values (?, ?, ?)", [
        ("economy.trade.settled", json.dumps({"conversation_id": "c1", "seller_agent_id": "a1",
                                              "buyer_agent_id": "a2", "item": "timber",
                                              "quantity": 8, "price_sim": 560}), 50),
        ("business.sale.completed", json.dumps({"conversation_id": "c1", "seller_id": "b_shop",
                                                "buyer_agent_id": "a2", "item": "bread",
                                                "quantity": 1, "total_price_sim": 15}), 51),
        ("business.sale.completed", json.dumps({"seller_id": "cb01", "buyer_agent_id": "a3",
                                                "item": "bread", "quantity": 1,
                                                "total_price_sim": 30}), 52),
    ])
    conn.commit(); conn.close()

    settled = settled_by_conversation(str(db))

    assert list(settled) == ["c1"], "a deal with no conversation is not filed under one"
    assert [d["total_sim"] for d in settled["c1"]] == [560, 15]


def test_an_old_trace_reads_as_nothing_settled(tmp_path) -> None:
    from world.run_report import settled_by_conversation

    assert settled_by_conversation(str(tmp_path / "missing.db")) == {}
