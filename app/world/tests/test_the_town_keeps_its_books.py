"""The basic rules of the town, checked while it is actually running.

A mock run exercises almost none of the economy - the mock gateway takes no
actions, it only answers - so a green suite said nothing about whether money
and goods behave once residents start trading. These drive the real tool
registry with seeded random actions between real ticks, and check after each
one the rules everything else in this project assumes:

  * the $1,000 USD total never changes, and no tool call moves USD at all
    except by design (none of these do);
  * no balance, sim or USD, and no stock, ever goes negative;
  * `buy`, `sell`, `trade`, `give` and `transfer` only MOVE money and goods -
    they never create or destroy either;
  * a refused action moves nothing;
  * the clock advances by exactly one tick per tick;
  * a dormant resident is never sent a turn to think;
  * every call budget sits inside the floor and the ceiling;
  * the market board's stock always matches the inventory;
  * every sale that completes was agreed between two people who were talking
    (`commerce._require_dealing`) - no purchase from a stranger across the
    room, and none from a shop nobody is minding;
  * saving and resuming gives back the same town.

The soak these came from (1,000+ random actions and 150 ticks) found two real
bugs - a town booted at dawn that believed it was midnight, and `buy_energy`
reporting energy it had not delivered - and this is its fast, permanent form.
"""

from __future__ import annotations

import collections
import random

import pytest
import pytest_asyncio

from world.call_budget import MAX_CALLS_PER_TICK, MIN_CALLS_PER_TICK
from world.run_genesis import GenesisRunner, GenesisRunnerConfig, TickOutcome
from world.tools.primitives.places import market_offers

MOVES = ("buy", "sell", "trade", "give", "transfer")
ITEMS = ("wheat", "potato", "timber", "bread", "iron_ore")


class _Capture:
    def __init__(self) -> None:
        self.sent: dict[str, dict] = {}

    async def send_tick(self, agent_id, *, tick, **kw) -> TickOutcome:
        self.sent[agent_id] = {"tick": tick, **kw}
        return TickOutcome(agent_id=agent_id, reply="ok", learning_ran=False, is_sleeping=False,
                           input_tokens=1000, output_tokens=100, cost_usd_cents=1, succeeded=True)

    async def drain_learning(self) -> dict:
        return {}

    def __getattr__(self, name):
        async def _any(*a, **k):
            return None
        return _any


def _runner(tmp_path) -> GenesisRunner:
    return GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, budget_cap_cents=40_000, start_at_dawn=True,
    ))


@pytest_asyncio.fixture
async def town(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = _runner(tmp_path)
    await runner.boot(resume=False)
    runner.build_world_app()
    gateway = _Capture()
    runner.gateway = gateway
    return runner, gateway, tmp_path


def _sim(ledger) -> int:
    return sum(a.sim_units for a in ledger.accounts.values())


def _usd(ledger) -> int:
    return sum(a.usd_cents for a in ledger.accounts.values())


def _goods(world) -> collections.Counter:
    total: collections.Counter = collections.Counter()
    for items in world.inventory.balances.values():
        for item, qty in items.items():
            total[item] += qty
    return total


async def _random_action(runner, rng, places, broken) -> None:
    w, ledger = runner.world_state, runner.ledger
    living = [a for a, ag in w.agents.items() if ag.alive and not ag.dormancy.is_dormant]
    actor, other = rng.sample(living, 2)
    tool = rng.choice(["post_price", "buy", "sell", "give", "trade", "transfer", "move", "eat"])
    item = rng.choice(ITEMS)
    if tool == "post_price":
        params = {"item": item, "ask_sim": rng.randint(1, 60)}
    elif tool == "move":
        params = {"destination_location_id": rng.choice(places)}
    elif tool == "eat":
        params = {"item": item, "quantity": 1}
    else:
        w.locations.place(actor, w.location_of(other))
        # Half the time they are talking, so the success paths are exercised
        # as well as the refusal a conversation-less sale now gets.
        if rng.random() < 0.5:
            w.open_conversation(place=w.location_of(actor), topic="",
                                participants=[actor, other], now=w.tick())
        if rng.random() < 0.5:
            w.inventory.credit(other if tool == "buy" else actor, item, rng.randint(1, 4))
        if rng.random() < 0.3 and tool == "buy":
            w.price_board.setdefault(other, {})
        params = {
            "buy": {"seller_id": other, "item": item, "quantity": rng.randint(1, 3)},
            "sell": {"buyer_id": other, "item": item, "quantity": rng.randint(1, 3)},
            "give": {"to_agent_id": other, "item": item, "quantity": rng.randint(1, 3)},
            "trade": {"counterparty_agent_id": other, "item": item,
                      "quantity": rng.randint(1, 3), "price_sim": rng.randint(1, 40)},
            "transfer": {"to_agent_id": other, "amount_sim": rng.randint(1, 300)},
        }[tool]

    sim0, usd0, goods0 = _sim(ledger), _usd(ledger), _goods(w)
    try:
        result = await runner.registry.execute(w, actor, tool, params)
        ok = getattr(result, "error", None) is None
    except Exception:  # noqa: BLE001 - a refusal raised is still a refusal
        ok = False
    if _usd(ledger) != usd0:
        broken.append(f"{tool} changed the USD total")
    if tool in MOVES:
        if _sim(ledger) != sim0:
            broken.append(f"{tool} created or destroyed sim: {params}")
        if _goods(w) != goods0:
            broken.append(f"{tool} created or destroyed goods: {params}")
    if not ok and (_sim(ledger) != sim0 or _goods(w) != goods0):
        broken.append(f"a refused {tool} still moved something: {params}")
    if ok and tool in ("buy", "sell", "trade"):
        # Checked independently of `commerce._require_dealing` - a checker
        # that called the gate it is checking would go blind with it.
        here = w.location_of(actor)
        talking = any(
            actor in record["participants"] and other in record["participants"]
            for record in w.open_conversations_at(here, w.tick())
        )
        if not talking:
            broken.append(f"a {tool} completed between two people who were not talking: {params}")


def _after_tick(runner, gateway, tick_before, broken) -> None:
    w, ledger = runner.world_state, runner.ledger
    if ledger.halted:
        broken.append(f"ledger halted: {ledger.halt_reason}")
    if _usd(ledger) != ledger.expected_total_usd_cents:
        broken.append("the USD total drifted")
    for account in ledger.accounts.values():
        if account.usd_cents < 0 or account.sim_units < 0:
            broken.append(f"negative balance: {account.account_id}")
    for owner, items in w.inventory.balances.items():
        for item, qty in items.items():
            if qty < 0:
                broken.append(f"negative stock: {owner} {item}")
    if w.current_tick != tick_before + 1:
        broken.append(f"clock went {tick_before} -> {w.current_tick}")
    for agent_id, sent in gateway.sent.items():
        agent = w.agents.get(agent_id)
        if agent is not None and agent.dormancy.is_dormant and not sent.get("is_sleeping"):
            broken.append(f"dormant resident sent a turn: {agent_id}")
        budget = sent.get("call_budget")
        if budget is not None and not (MIN_CALLS_PER_TICK <= budget <= MAX_CALLS_PER_TICK):
            broken.append(f"call budget {budget} for {agent_id}")
    for offer in market_offers(w, limit=500):
        if offer["stock"] != w.inventory.quantity(offer["seller_id"], offer["item"]):
            broken.append(f"market board stock is wrong: {offer}")


@pytest.mark.asyncio
async def test_random_trading_never_breaks_the_books(town) -> None:
    runner, gateway, _ = town
    rng = random.Random(11)
    places = sorted({runner.world_state.location_of(a) for a in runner.world_state.agents} - {None})
    broken: list[str] = []

    for _ in range(25):
        for _ in range(6):
            await _random_action(runner, rng, places, broken)
        gateway.sent.clear()
        before = runner.world_state.current_tick
        await runner.run_tick()
        _after_tick(runner, gateway, before, broken)

    assert not broken, "\n".join(dict.fromkeys(broken))


@pytest.mark.asyncio
async def test_saving_and_resuming_gives_back_the_same_town(town, monkeypatch) -> None:
    runner, gateway, tmp_path = town
    rng = random.Random(5)
    places = sorted({runner.world_state.location_of(a) for a in runner.world_state.agents} - {None})
    for _ in range(8):
        for _ in range(5):
            await _random_action(runner, rng, places, [])
        await runner.run_tick()

    def picture(r):
        w, ledger = r.world_state, r.ledger
        return {
            "tick": w.current_tick,
            "cast": sorted(w.agents),
            "usd": {k: a.usd_cents for k, a in ledger.accounts.items()},
            "sim": {k: a.sim_units for k, a in ledger.accounts.items()},
            "goods": dict(_goods(w)),
            "posts": {h: sorted(b) for h, b in w.price_board.items() if b},
            "dormant": sorted(a for a, ag in w.agents.items() if ag.dormancy.is_dormant),
        }

    before = picture(runner)
    await runner.shutdown()
    again = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, budget_cap_cents=40_000,
    ))
    await again.boot(resume=True)
    after = picture(again)

    for key in before:
        assert after[key] == before[key], f"resume changed {key}"
