"""No single `buy_energy` can leave a resident with nothing to act with.

Run 26, tick 56: Giselle Ingram converted all 4,900 of her sim into energy
in one call. For the rest of the run the world refused her `move`, `rest`,
`say`, `find_people`, `describe_person` and `draw_from_till`, and at the day
boundary her household paid 0 of 90 sim upkeep. She could think for days and
could not take a step. Programs were already held to 50% of the caller's
wallet for this exact reason; converting sim to energy was not, and energy
cannot be turned back into sim.
"""

from __future__ import annotations

import pytest

from world.ledger.core import TREASURY_ACCOUNT_ID
from world.tools.primitives.civic import ENERGY_PURCHASE_MAX_SHARE_PERCENT

from .conftest import spawn_agent

COMPLEX = "government_complex"


@pytest.mark.asyncio
async def test_giselles_purchase_is_refused_and_moves_nothing(world, registry):
    await spawn_agent(world, "a0047", COMPLEX, starting_sim=4900)

    with pytest.raises(Exception) as raised:
        await registry.execute(world, "a0047", "buy_energy", {"sim_amount": 4900})

    assert f"{ENERGY_PURCHASE_MAX_SHARE_PERCENT}%" in str(raised.value), str(raised.value)
    assert world.ledger.sim_balance("a0047") == 4900, "a refused purchase moved sim"


@pytest.mark.asyncio
async def test_a_purchase_within_the_ceiling_goes_through(world, registry):
    await spawn_agent(world, "a0047", COMPLEX, starting_sim=5000)
    # The store pays out of the treasury's USD, and a bare test world starts
    # with none (the same line `test_a_zero_margin_changes_nothing` needs).
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000
    energy_before = world.ledger.usd_balance("a0047")

    outcome = await registry.execute(world, "a0047", "buy_energy", {"sim_amount": 2500})

    assert outcome.result["energy_bought"] > 0
    assert world.ledger.usd_balance("a0047") > energy_before
    assert world.ledger.sim_balance("a0047") <= 2500


@pytest.mark.asyncio
async def test_the_refusal_states_the_rule_not_a_strategy(world, registry):
    """What was refused and why - not advice about what to do instead."""
    await spawn_agent(world, "a0047", COMPLEX, starting_sim=1000)

    with pytest.raises(Exception) as raised:
        await registry.execute(world, "a0047", "buy_energy", {"sim_amount": 1000})

    message = str(raised.value)
    assert "cannot be turned back" in message
    assert "should" not in message.lower()


def test_the_ceiling_is_described_where_the_resident_reads_it(registry):
    assert f"{ENERGY_PURCHASE_MAX_SHARE_PERCENT}%" in registry.tools["buy_energy"].description
