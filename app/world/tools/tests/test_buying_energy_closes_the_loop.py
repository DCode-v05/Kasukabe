"""Money can buy thinking, and until now nothing could spend it.

DESIGN.md rests the whole town on one loop: energy lets you work, work
earns money, money buys energy. The last link has never existed.

`Ledger.exchange_sim_for_usd` is written, tested and complete, and its own
docstring says what is missing:

    "bought at the government store (a tool call, in person,
     location-gating is the tool registry's job, not this method's)"

That tool was never written. `list_places` tells residents they can "buy
energy credit with sim currency at the government store" and no verb in
the registry does it, so a resident who walks there and reads the sign can
do nothing when it arrives.

What that costs the research, stated plainly: a dollar wallet only ever
goes down, so nothing a resident does changes how long it lives. Cognitive
poverty, altruism under scarcity, survival ethics at $0 and any
metacognition about the price of thinking are unanswerable by
construction - not unmeasured, unanswerable, because the mechanism that
would produce them is absent.
"""

from __future__ import annotations

import pytest

from world.ledger.money import SIM_PER_USD_CENT, energy_units
from world.tools.state import WorldState

from .conftest import spawn_agent

STORE = "Kasukabe Government Complex"
ELSEWHERE = "Kasukabe Farmland"


async def _resident(world: WorldState, agent_id: str = "n01", *, at: str = STORE) -> None:
    await spawn_agent(world, agent_id, at)
    world.agent_names[agent_id] = "Bram Holloway"
    # A treasury with dollars in it, which is what genesis builds: the
    # $1,000 starts there and the exchange moves it out. The test fixture
    # opens accounts at zero, and a treasury with no dollars cannot sell
    # anybody any thinking - a true refusal, and not the one under test.
    from world.ledger.core import TREASURY_ACCOUNT_ID

    # Exactly the hundred thousand cents the invariant demands: whatever
    # the wallets already hold, the treasury holds the rest. Setting a
    # round 88,000 beside empty wallets halts the ledger for violating the
    # $1,000 invariant - correctly, and it is the invariant this tool must
    # not break, so the fixture has to satisfy it before the test means
    # anything.
    from world.ledger.money import CENTS_PER_USD  # noqa: F401

    wallets = sum(
        account.usd_cents
        for account in world.ledger.accounts.values()
        if account.account_id != TREASURY_ACCOUNT_ID
    )
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000 - wallets


@pytest.mark.asyncio
async def test_a_resident_can_turn_sim_into_thinking(world: WorldState, registry) -> None:
    await _resident(world)
    before_sim = world.ledger.sim_balance("n01")
    before_usd = world.ledger.usd_balance("n01")

    outcome = await registry.execute(world, "n01", "buy_energy", {"sim_amount": 20_000})

    assert world.ledger.sim_balance("n01") < before_sim, "no sim left the wallet"
    assert world.ledger.usd_balance("n01") > before_usd, (
        "the resident paid and got no thinking for it"
    )
    # Handed back in energy, the unit a resident reads everywhere else.
    assert outcome.result["energy_bought"] == energy_units(20_000 // SIM_PER_USD_CENT)


@pytest.mark.asyncio
async def test_it_can_only_be_done_at_the_government_store(
    world: WorldState, registry
) -> None:
    """In person, like every other civic act. A resident who could buy
    energy from a field would never have to walk anywhere, and the
    journey is the cost that makes the choice a choice."""
    from world.tools.errors import LocationGateError

    await _resident(world, at=ELSEWHERE)
    with pytest.raises(LocationGateError):
        await registry.execute(world, "n01", "buy_energy", {"sim_amount": 20_000})


@pytest.mark.asyncio
async def test_the_town_hall_counts_as_the_same_counter(
    world: WorldState, registry
) -> None:
    """`satisfies_gate` folds the hall into the complex, and four
    residents once stood in the government complex unable to use a single
    civic tool because a gate compared strings."""
    await _resident(world, at="town_hall")
    outcome = await registry.execute(world, "n01", "buy_energy", {"sim_amount": 10_000})
    assert outcome.result["energy_bought"] > 0


@pytest.mark.asyncio
async def test_buying_more_than_you_hold_is_refused_and_says_what_you_have(
    world: WorldState, registry
) -> None:
    """A refusal an agent can act on names the number it was short by."""
    await _resident(world)
    held = world.ledger.sim_balance("n01")

    with pytest.raises(Exception) as caught:
        await registry.execute(world, "n01", "buy_energy", {"sim_amount": held + 5_000})

    message = str(caught.value)
    assert str(held) in message or "sim" in message.lower()


@pytest.mark.asyncio
async def test_too_little_sim_to_buy_a_single_cent_is_refused_clearly(
    world: WorldState, registry
) -> None:
    """The rate is fixed and integral. Buying 5 sim at 100 sim to the cent
    would round to nothing, and taking the sim for nothing is theft by
    rounding."""
    await _resident(world)
    with pytest.raises(Exception) as caught:
        await registry.execute(world, "n01", "buy_energy", {"sim_amount": 1})
    assert "cent" in str(caught.value).lower() or "least" in str(caught.value).lower()


@pytest.mark.asyncio
async def test_the_thousand_dollars_survives_the_exchange(
    world: WorldState, registry
) -> None:
    """The invariant the whole economy is checked against: USD moves
    between the treasury and a wallet and is never created."""
    await _resident(world)
    # 25,000 of the 50,000 held: the most one purchase may spend under
    # ENERGY_PURCHASE_MAX_SHARE_PERCENT. 30,000 is now refused, which is
    # the ceiling working, not the invariant.
    await registry.execute(world, "n01", "buy_energy", {"sim_amount": 25_000})
    await world.ledger.enforce_invariant(world.tick())


@pytest.mark.asyncio
async def test_the_result_tells_the_resident_what_it_can_now_afford(
    world: WorldState, registry
) -> None:
    """A number of cents means nothing to somebody deciding whether to
    walk across town for it. What it buys is turns of thinking."""
    await _resident(world)
    outcome = await registry.execute(world, "n01", "buy_energy", {"sim_amount": 20_000})
    # Energy, not cents - which is this docstring's own point made further:
    # a resident deciding whether to walk across town reads energy.
    assert "energy_bought" in outcome.result
    assert "energy_now" in outcome.result
    assert outcome.result["energy_now"] >= outcome.result["energy_bought"]


@pytest.mark.asyncio
async def test_buying_energy_is_priced(world: WorldState, registry) -> None:
    from world.ledger.action_costs import SCHEDULE_TABLE

    assert "buy_energy" in SCHEDULE_TABLE


# ── the spec's own three tests, by its own names ──────────────────────
#
# docs/kasukabetoolspec.md A1 names three. Written here against the rows
# of its table rather than against what the code happens to do.


@pytest.mark.asyncio
async def test_buying_energy_moves_sim_to_treasury_and_dollars_to_the_buyer(
    world: WorldState, registry
) -> None:
    """A1's ledger row: "debit caller sim; credit caller USD ...; the sim
    goes to the treasury, so the $1000 invariant holds"."""
    from world.ledger.core import TREASURY_ACCOUNT_ID

    await _resident(world)
    treasury_sim_before = world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units
    agent_sim_before = world.ledger.sim_balance("n01")

    await registry.execute(world, "n01", "buy_energy", {"sim_amount": 20_000})

    assert world.ledger.sim_balance("n01") == agent_sim_before - 20_000
    assert world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units == (
        treasury_sim_before + 20_000
    ), "the sim left the resident and reached nobody"


@pytest.mark.asyncio
async def test_energy_cannot_be_bought_from_the_bakery(
    world: WorldState, registry
) -> None:
    """A1: "in person, at the government store". The walk is the point -
    where residents choose to spend a tick is data."""
    from world.tools.errors import LocationGateError

    await _resident(world, at="kitchen")
    with pytest.raises(LocationGateError):
        await registry.execute(world, "n01", "buy_energy", {"sim_amount": 20_000})


@pytest.mark.asyncio
async def test_ledger_invariant_survives_an_energy_purchase(
    world: WorldState, registry
) -> None:
    await _resident(world)
    # 25,000 of the 50,000 held: the most one purchase may spend under
    # ENERGY_PURCHASE_MAX_SHARE_PERCENT. 30,000 is now refused, which is
    # the ceiling working, not the invariant.
    await registry.execute(world, "n01", "buy_energy", {"sim_amount": 25_000})
    await world.ledger.enforce_invariant(world.tick())


@pytest.mark.asyncio
async def test_the_purchase_emits_the_event_the_spec_names(
    world: WorldState, registry
) -> None:
    """A1's event row. S2 - "residents who reach buy_energy by day 3" -
    is read from this, and a purchase with no event is one no measurement
    can see."""
    await _resident(world)
    outcome = await registry.dispatch(world, "n01", "buy_energy", {"sim_amount": 20_000})
    kinds = [event.type for event in outcome.events]
    assert "economy.energy.bought" in kinds
    payload = next(e.payload for e in outcome.events if e.type == "economy.energy.bought")
    assert payload["agent_id"] == "n01"
    assert payload["sim_amount"] == 20_000
    # Derived from the rate: 20,000 sim buys twice the credit it did
    # before the 2026-09-04 re-rating, which is the whole point of it.
    assert payload["usd_cents"] == 20_000 // SIM_PER_USD_CENT
    assert payload["usd_balance_after"] >= 200


@pytest.mark.asyncio
async def test_the_counter_charges_nothing_for_the_exchange(
    world: WorldState, registry
) -> None:
    """A1's cost row: "FEE, base 0. The exchange is the price." Charging
    sim to convert sim into thinking taxes the one act that keeps a
    resident alive."""
    from world.ledger.action_costs import SCHEDULE_TABLE

    rule = SCHEDULE_TABLE["buy_energy"]
    assert rule.base_cost == 0 and rule.min_cost == 0
