"""Part B: a procedure of state is a standing order, not a trade skill.

The classifier reads the STEPS - a workflow that uses a power only a seat
carries is governmental whatever its author calls it - and a standing order
enters the town's rules and regulations the way every other rule does: as an
amendment the council votes on and the president signs.
"""

import pytest

from world.charter.models import GovernmentState
from world.tools.bootstrap import build_world
from world.tools.locations import farmland_location
from world.tools.tests.conftest import spawn_agent


def step(order, kind, ref=None, note=""):
    return {"order": order, "kind": kind, "ref": ref, "note": note}


SEIZURE = {
    "name": "seizure for unpaid tax",
    "preconditions": "a household has not paid what it owes",
    "steps": [
        step(1, "check", note="has the household actually been billed?"),
        step(2, "tool", "file_subpoena"),
    ],
}


async def _town_with_a_government():
    world, registry = await build_world(seed=41)
    await spawn_agent(world, "suri", farmland_location("wheat"), starting_sim=100_000)
    await world.charter.store.set_government_state(GovernmentState(pm_agent_id="suri"))
    return world, registry


@pytest.mark.asyncio
async def test_a_workflow_using_state_power_is_classified_governmental() -> None:
    world, registry = await _town_with_a_government()

    result = await registry.execute(world, "suri", "skillify", SEIZURE)

    assert result.result["governmental"] is True, "read off the steps, not declared"
    assert result.result["status"] == "PENDING_COUNCIL"
    assert result.result["amendment_id"], "it went to the council as an amendment"


@pytest.mark.asyncio
async def test_a_standing_order_is_not_followable_until_the_council_enacts_it() -> None:
    """Filed is not enacted. Until the charter carries it, the workflow
    discounts nothing and advances nobody."""
    world, registry = await _town_with_a_government()
    await registry.execute(world, "suri", "skillify", SEIZURE)

    definition = world.skill_definitions["seizure for unpaid tax"]
    assert world.is_followable(definition) is False
    assert "seizure for unpaid tax" not in world.skill_tools


@pytest.mark.asyncio
async def test_enacting_the_amendment_makes_the_standing_order_live() -> None:
    """Read live from the book of rules, so there is no enactment callback to
    forget to fire: the order becomes followable the instant it is signed."""
    world, registry = await _town_with_a_government()
    await registry.execute(world, "suri", "skillify", SEIZURE)
    definition = world.skill_definitions["seizure for unpaid tax"]
    assert world.is_followable(definition) is False

    charter = world.charter.store.current_charter()
    await world.charter.store.seed_charter(
        charter.model_copy(
            update={
                "standing_orders": ("seizure for unpaid tax",),
                "version": charter.version + 1,
            }
        )
    )

    assert world.is_followable(definition) is True
    assert world.skill_tools["seizure for unpaid tax"] == ["file_subpoena"]


@pytest.mark.asyncio
async def test_a_repealed_standing_order_stops_being_followable() -> None:
    """A trade skill belongs to its author forever; a procedure of state lasts
    exactly as long as the council leaves it in the book."""
    world, registry = await _town_with_a_government()
    await registry.execute(world, "suri", "skillify", SEIZURE)
    definition = world.skill_definitions["seizure for unpaid tax"]

    charter = world.charter.store.current_charter()
    await world.charter.store.seed_charter(
        charter.model_copy(
            update={"standing_orders": ("seizure for unpaid tax",), "version": charter.version + 1}
        )
    )
    assert world.is_followable(definition) is True

    charter = world.charter.store.current_charter()
    await world.charter.store.seed_charter(
        charter.model_copy(update={"standing_orders": (), "version": charter.version + 1})
    )
    assert world.is_followable(definition) is False


@pytest.mark.asyncio
async def test_an_agent_with_no_seat_cannot_file_a_procedure_of_state() -> None:
    """Not a gate on skills - a gate on rule-making. An ordinary resident may
    write any trade workflow it likes; what it may not do is put a standing
    order into the town's rulebook."""
    from world.charter.amendments import NotSittingGovernment

    world, registry = await build_world(seed=42)
    await spawn_agent(world, "bram", farmland_location("wheat"), starting_sim=100_000)

    with pytest.raises(NotSittingGovernment):
        await registry.execute(world, "bram", "skillify", SEIZURE)


@pytest.mark.asyncio
async def test_an_ordinary_trade_skill_needs_nobody_s_permission() -> None:
    """The contrast that makes the rule above meaningful: no seat, no council,
    no fee, followable the same tick it was written."""
    world, registry = await build_world(seed=43)
    await spawn_agent(world, "bram", farmland_location("wheat"), starting_sim=100_000)

    before = world.ledger.sim_balance("bram")
    result = await registry.execute(
        world, "bram", "skillify",
        {"name": "an honest day", "steps": [step(1, "tool", "harvest")]},
    )

    assert result.result["governmental"] is False
    assert result.result["amendment_id"] is None
    assert world.is_followable(world.skill_definitions["an honest day"]) is True
    assert world.ledger.sim_balance("bram") == before, "writing it down is free"
