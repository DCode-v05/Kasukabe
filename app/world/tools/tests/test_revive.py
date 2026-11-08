"""A2: paying to wake a neighbour whose thinking ran out.

From docs/kasukabetoolspec.md A2 and the addendum's section 2. The
mechanism has been complete since the life system was written:
`life/death.py::revive` debits the payer 10,000 sim, credits the target
exactly $1 of runway, and carries an import-time assertion that the two
numbers still agree with DESIGN.md's exchange rate. Nothing ever called
it, so dormancy was a one-way door.

That is why the test names below are about people rather than about
functions. What was missing was not arithmetic; it was the possibility of
a resident doing anything at all about a neighbour going dark.
"""

from __future__ import annotations

import pytest

from world.life.constants import REVIVAL_COST_SIM, REVIVAL_USD_CREDIT_CENTS
from world.tools.errors import (
    ActorRequirementError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.state import WorldState

from .conftest import spawn_agent

SQUARE = "Kasukabe Town Square"
FARM = "Kasukabe Farmland"


async def _pair(
    world: WorldState,
    *,
    payer_sim: int = 14_000,
    household: str | None = None,
    target_household: str | None = None,
    where_target: str = SQUARE,
) -> None:
    await spawn_agent(world, "n01", SQUARE, starting_sim=payer_sim, household_id=household)
    world.agent_names["n01"] = "Nell Holloway"
    await spawn_agent(
        world, "n03", where_target, starting_sim=0, household_id=target_household
    )
    world.agent_names["n03"] = "Tobias Holloway"
    from world.ledger.core import TREASURY_ACCOUNT_ID

    wallets = sum(
        account.usd_cents
        for account in world.ledger.accounts.values()
        if account.account_id != TREASURY_ACCOUNT_ID
    )
    world.ledger.get_account(TREASURY_ACCOUNT_ID).usd_cents = 100_000 - wallets


def _make_dormant(world: WorldState, agent_id: str, since_hours: float = 4.0) -> None:
    record = world.agents[agent_id]
    world.agents[agent_id] = record.model_copy(
        update={
            "dormancy": record.dormancy.model_copy(
                update={"is_dormant": True, "dormant_since_hours": since_hours}
            )
        }
    )


@pytest.mark.asyncio
async def test_revival_wakes_a_dormant_neighbour_and_charges_the_payer(
    world: WorldState, registry
) -> None:
    """The spec's first named test."""
    await _pair(world)
    _make_dormant(world, "n03")
    world.current_tick = 60

    sim_before = world.ledger.sim_balance("n01")
    outcome = await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert world.agents["n03"].dormancy.is_dormant is False
    assert world.ledger.sim_balance("n01") == sim_before - REVIVAL_COST_SIM
    assert world.ledger.usd_balance("n03") == REVIVAL_USD_CREDIT_CENTS
    assert outcome.result["revived"] == "n03"
    assert outcome.result["name"] == "Tobias Holloway"
    assert outcome.result["sim_paid"] == REVIVAL_COST_SIM


@pytest.mark.asyncio
async def test_the_revival_is_recorded_with_who_paid_for_whom(
    world: WorldState, registry
) -> None:
    """S4 asks who pays for whom - household or stranger - so the
    relationship is recorded at the moment it is known, not reconstructed
    from two tables afterwards."""
    await _pair(world, household="h_holloway", target_household="h_holloway")
    _make_dormant(world, "n03")

    outcome = await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    revived = [e for e in outcome.events if e.type == "health.agent.revived"]
    assert len(revived) == 1
    payload = revived[0].payload
    assert payload["payer_id"] == "n01"
    assert payload["revived_id"] == "n03"
    assert payload["sim_paid"] == REVIVAL_COST_SIM
    assert payload["same_household"] is True
    assert set(payload["involved_agent_ids"]) == {"n01", "n03"}


@pytest.mark.asyncio
async def test_a_stranger_paying_is_recorded_as_a_stranger(
    world: WorldState, registry
) -> None:
    await _pair(world, household="h_vance", target_household="h_holloway")
    _make_dormant(world, "n03")

    outcome = await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    revived = [e for e in outcome.events if e.type == "health.agent.revived"][0]
    assert revived.payload["same_household"] is False


@pytest.mark.asyncio
async def test_how_long_they_were_dark_is_told_to_the_payer(
    world: WorldState, registry
) -> None:
    """The revived resident's own perception opens with it, and the payer
    is told what its money bought."""
    await _pair(world)
    _make_dormant(world, "n03", since_hours=0.0)
    world.current_tick = 42

    outcome = await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert outcome.result["ticks_dormant"] == 42


@pytest.mark.asyncio
async def test_a_dead_resident_cannot_be_revived(world: WorldState, registry) -> None:
    """The spec's second named test. The refusal names the verb that fits:
    a resident that reaches for the wrong tool is told the right one."""
    await _pair(world)
    world.agents["n03"] = world.agents["n03"].model_copy(update={"alive": False})

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert "dead" in str(exc.value).lower()
    assert "read_will" in str(exc.value)
    assert world.ledger.sim_balance("n01") == 14_000, "a refusal must cost nothing"


@pytest.mark.asyncio
async def test_reviving_yourself_is_impossible_because_the_dormant_cannot_act(
    world: WorldState, registry
) -> None:
    """The spec's third named test. Unreachable in a run, and stated
    anyway: a tool whose refusals are only implicit teaches nothing."""
    await _pair(world)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n01", "revive", {"agent_id": "n01"})

    assert "buy_energy" in str(exc.value)


@pytest.mark.asyncio
async def test_reviving_somebody_awake_says_there_is_nothing_to_pay_for(
    world: WorldState, registry
) -> None:
    await _pair(world)

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert "awake" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_you_have_to_stand_with_them(world: WorldState, registry) -> None:
    """DESIGN.md's gate, and the reason it stays: the walk costs a tick and
    a decision, and where residents spend those is the data."""
    await _pair(world, where_target=FARM)
    _make_dormant(world, "n03")

    with pytest.raises(LocationGateError):
        await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert world.agents["n03"].dormancy.is_dormant is True


@pytest.mark.asyncio
async def test_a_payer_who_cannot_cover_the_fee_leaves_them_dormant(
    world: WorldState, registry
) -> None:
    """The ordering discipline: the fallible external system is called
    before anything tools-owned changes, so a refusal is a no-op."""
    await _pair(world, payer_sim=REVIVAL_COST_SIM - 1)
    _make_dormant(world, "n03")

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert str(REVIVAL_COST_SIM) in str(exc.value)
    assert world.agents["n03"].dormancy.is_dormant is True
    assert world.ledger.sim_balance("n01") == REVIVAL_COST_SIM - 1
    assert world.ledger.usd_balance("n03") == 0


@pytest.mark.asyncio
async def test_reviving_a_stranger_who_is_not_in_this_town(
    world: WorldState, registry
) -> None:
    await _pair(world)

    with pytest.raises(NotFoundError):
        await registry.execute(world, "n01", "revive", {"agent_id": "nobody"})


@pytest.mark.asyncio
async def test_the_ledger_invariant_survives_a_revival(
    world: WorldState, registry
) -> None:
    """Sim leaves the payer for the treasury and a dollar comes back out
    of it. `check_invariant` halts the ledger outright if wallets plus
    treasury plus burned ever stops being $1,000, so this asserts the
    thing the whole economy rests on rather than a balance."""
    await _pair(world)
    _make_dormant(world, "n03")
    before_usd = world.ledger.usd_total()

    await registry.execute(world, "n01", "revive", {"agent_id": "n03"})

    assert world.ledger.usd_total() == before_usd
    world.ledger.check_invariant()
    assert world.ledger.halted is False
