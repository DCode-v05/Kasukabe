"""Fees reach whoever earned them, and the government's margin is real.

`ECON-29`. `LedgerEconomyPort` sent every life-initiated debit to the
treasury. So a resident who paid a pharmacist for medicine paid the
GOVERNMENT, and the pharmacy - a real business, in genesis, with its own
ledger account - earned nothing from the one service it exists to sell.
A shop whose income is confiscated at the counter cannot be studied as a
business.

Keyed on business TYPE, not on `b_pharmacy`: that is one genesis file's
spelling, and a town that founds a second pharmacy should be able to take
the money.

The wedding fee still goes to the treasury, and that is an answer rather
than a leftover: the temple is a PLACE on the map, not a business with an
account, so there is nobody else to pay. It is written down in
`payee_for` so the next reader does not have to infer it from silence.

`ECON-30`. `credit_store_margin_bps` is a power DESIGN section 7 gives
the government. It has a setter, a validator and an API field, and had no
READER - a government could set it, the treasury view would echo it back,
and every `buy_energy` still exchanged at the fixed rate. A lever with no
linkage is worse than no lever, because it reads as a policy the town
has.

The margin is retained by the treasury, so a government that raises it
makes cognition dearer for everybody and funds itself doing so. That
trade-off is the whole point of the power.
"""
from __future__ import annotations

import pytest

from world.ledger.core import TREASURY_ACCOUNT_ID
from world.ledger.models import TransactionReason
from world.tools.economy_bridge import LedgerEconomyPort

from .test_construction import spawn_agent  # noqa: F401


def test_a_fee_with_no_trade_behind_it_goes_to_the_town() -> None:
    """A wedding has no payee: the temple is a place, not a business."""
    port = LedgerEconomyPort(ledger=None, tick_provider=lambda: 0, world=None)
    assert port.payee_for("temple_wedding_fee") == TREASURY_ACCOUNT_ID
    assert port.payee_for("daily_upkeep") == TREASURY_ACCOUNT_ID


@pytest.mark.asyncio
async def test_the_pharmacist_is_paid_for_the_medicine(world) -> None:
    """The finding. Before this the money went to the government."""
    from world.tools.models import Business

    await spawn_agent(world, "chemist", "business:b_pharmacy")
    world.businesses["b_pharmacy"] = Business(
        business_id="b_pharmacy", name="Pharmacy", business_type="pharmacy",
        owner_agent_id="chemist", employee_agent_ids=set(),
        location_id="business:b_pharmacy",
    )
    world.ledger.open_business("b_pharmacy", "chemist")

    port = LedgerEconomyPort(world.ledger, world.tick, world)
    assert port.payee_for("pharmacy_treatment") == "b_pharmacy", (
        "ECON-29: the treatment fee still goes to the treasury, so the "
        "pharmacy earns nothing from the only thing it sells"
    )


@pytest.mark.asyncio
async def test_a_town_with_no_pharmacy_still_charges(world) -> None:
    """A resident should not be refused treatment because nobody has
    opened a pharmacy - the town takes the fee and the treatment happens."""
    port = LedgerEconomyPort(world.ledger, world.tick, world)
    assert port.payee_for("pharmacy_treatment") == TREASURY_ACCOUNT_ID


@pytest.mark.asyncio
async def test_a_business_with_no_ledger_account_is_not_paid(world) -> None:
    """A `Business` row without an account would make `move` raise, and
    an unpayable payee must not turn a treatment into an exception."""
    from world.tools.models import Business

    world.businesses["b_ghost"] = Business(
        business_id="b_ghost", name="Ghost", business_type="pharmacy",
        owner_agent_id="nobody", employee_agent_ids=set(),
        location_id="business:b_ghost",
    )
    port = LedgerEconomyPort(world.ledger, world.tick, world)
    assert port.payee_for("pharmacy_treatment") == TREASURY_ACCOUNT_ID


def test_a_port_with_no_world_behaves_exactly_as_before() -> None:
    """The safety property. Six call sites were updated; a seventh that
    somebody adds without a world must not crash - it just pays the town,
    which is what every one of them did before this change."""
    port = LedgerEconomyPort(ledger=None, tick_provider=lambda: 0)
    assert port.payee_for("pharmacy_treatment") == TREASURY_ACCOUNT_ID


@pytest.mark.asyncio
async def test_a_zero_margin_changes_nothing(world, registry) -> None:
    """Genesis sets no margin, so every existing run must be unaffected."""
    from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX

    await spawn_agent(world, "buyer", LOCATION_GOVERNMENT_COMPLEX)
    world.ledger.get_account("buyer").sim_units = 50_000
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000
    before = world.ledger.get_account("buyer").usd_cents

    await registry.execute(
        world, "buyer", "buy_energy", {"sim_amount": 20_000}
    )

    assert world.ledger.get_account("buyer").usd_cents > before
    margins = [
        t for t in world.ledger.transactions if t.memo == "credit_store_margin"
    ]
    assert not margins, "a margin was charged when the charter sets none"


@pytest.mark.asyncio
async def test_the_margin_is_charged_and_kept_by_the_treasury(
    world, registry
) -> None:
    """The finding. Before this the setter wrote a number nothing read."""
    from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX

    policy = await world.charter.store.get_policy()
    await world.charter.store.push_policy(
        policy.model_copy(update={"credit_store_margin_bps": 1_000, "version": 1})
    )

    await spawn_agent(world, "buyer", LOCATION_GOVERNMENT_COMPLEX)
    world.ledger.get_account("buyer").sim_units = 50_000
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000
    treasury_before = world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units

    await registry.execute(world, "buyer", "buy_energy", {"sim_amount": 20_000})

    margins = [
        t for t in world.ledger.transactions if t.memo == "credit_store_margin"
    ]
    assert margins, (
        "ECON-30: the government set a credit-store margin and buy_energy "
        "exchanged at the fixed rate anyway - the lever reads as policy "
        "the town has and is not linked to anything"
    )
    assert margins[-1].amount == 2_000, "10% of 20,000"
    assert margins[-1].reason is TransactionReason.FEE
    assert world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units >= (
        treasury_before + 2_000
    )


@pytest.mark.asyncio
async def test_a_margin_that_eats_the_whole_purchase_is_refused(
    world, registry
) -> None:
    """A resident whose sim buys less than a cent AFTER the margin must be
    told so, not silently charged the margin for nothing."""
    from world.tools.errors import ToolValidationError
    from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX

    policy = await world.charter.store.get_policy()
    await world.charter.store.push_policy(
        policy.model_copy(update={"credit_store_margin_bps": 9_900, "version": 1})
    )

    await spawn_agent(world, "buyer", LOCATION_GOVERNMENT_COMPLEX)
    world.ledger.get_account("buyer").sim_units = 50_000
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000
    before = world.ledger.get_account("buyer").sim_units

    with pytest.raises(ToolValidationError) as refused:
        await registry.execute(world, "buyer", "buy_energy", {"sim_amount": 1_000})

    assert "margin" in str(refused.value)
    assert world.ledger.get_account("buyer").sim_units == before, (
        "ECON-30: the purchase was refused and the resident paid the "
        "margin anyway"
    )


@pytest.mark.asyncio
async def test_the_money_actually_lands_in_the_pharmacy(world) -> None:
    """The wiring, not the resolver.

    Every other ECON-29 test above calls `payee_for` directly, and all of
    them stayed green when the debit was reverted to send the money to
    the treasury regardless - a resolver with no caller, which is the
    defect this whole review keeps finding. This one moves real money and
    looks where it went.
    """
    from world.tools.models import Business

    await spawn_agent(world, "chemist", "business:b_pharmacy")
    await spawn_agent(world, "patient", "business:b_pharmacy")
    world.businesses["b_pharmacy"] = Business(
        business_id="b_pharmacy", name="Pharmacy", business_type="pharmacy",
        owner_agent_id="chemist", employee_agent_ids=set(),
        location_id="business:b_pharmacy",
    )
    world.ledger.open_business("b_pharmacy", "chemist")
    world.ledger.get_account("patient").sim_units = 1_000

    shop_before = world.ledger.get_account("b_pharmacy").sim_units
    town_before = world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units

    port = LedgerEconomyPort(world.ledger, world.tick, world)
    await port.debit_sim("patient", 120, reason="pharmacy_treatment")

    assert world.ledger.get_account("b_pharmacy").sim_units == shop_before + 120, (
        "ECON-29: the fee was resolved to the pharmacy and paid to the "
        "treasury anyway - payee_for has no caller"
    )
    assert world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units == town_before


@pytest.mark.asyncio
async def test_upkeep_still_lands_in_the_treasury(world) -> None:
    """The control on the same path: a fee with no trade behind it goes
    to the town, and moving money is how we check it."""
    await spawn_agent(world, "villager", "town_hall")
    world.ledger.get_account("villager").sim_units = 1_000
    town_before = world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units

    port = LedgerEconomyPort(world.ledger, world.tick, world)
    await port.debit_sim("villager", 90, reason="daily_upkeep")

    assert world.ledger.get_account(TREASURY_ACCOUNT_ID).sim_units == (
        town_before + 90
    )


@pytest.mark.asyncio
async def test_the_energy_a_resident_is_told_it_bought_is_the_energy_it_got(
    world, registry
) -> None:
    """With a margin set, `buy_energy` told a resident it had bought energy
    for the FULL sim it spent - `sim_to_usd_cents(params.sim_amount)` - while
    the ledger exchanged only what was left after the margin. A soak with a
    10% margin: told 400 energy, received 360. The run report reads the same
    number off `economy.energy.bought`, so it overstated every purchase too.

    Latent while no government had set a margin, and exactly wrong the
    moment one did - which is the moment a resident most needs the truth
    about what its sim now buys.
    """
    from world.ledger.money import energy_units
    from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX

    policy = await world.charter.store.get_policy()
    await world.charter.store.push_policy(
        policy.model_copy(update={"credit_store_margin_bps": 1_000, "version": 1})
    )
    await spawn_agent(world, "buyer", LOCATION_GOVERNMENT_COMPLEX)
    world.ledger.get_account("buyer").sim_units = 50_000
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000
    before = world.ledger.usd_balance("buyer")

    result = await registry.execute(world, "buyer", "buy_energy", {"sim_amount": 2_000})

    received = world.ledger.usd_balance("buyer") - before
    told = result.result["energy_bought"]
    assert told == energy_units(received), f"told {told}, received {energy_units(received)}"
    bought = [e for e in world.event_log if e.type == "economy.energy.bought"] if hasattr(
        world, "event_log") else []
    for event in bought[-1:]:
        assert event.payload["usd_cents"] == received, "the report would overstate the purchase"
