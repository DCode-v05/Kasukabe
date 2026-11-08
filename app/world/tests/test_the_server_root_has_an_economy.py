"""The other composition root has to charge for living, too.

ECON-08. Kasukabe has two roots that advance world time. `world/
run_genesis.py`'s `GenesisRunner` is the one every launcher boots today
and it runs the whole day-boundary economy: upkeep, head tax, pensions,
wage reconciliation, the zero-wallet dormancy sweep. `world/main.py` +
`world/wiring.py`'s `WorldServer` is the root ROADMAP T3 says becomes
the only server, and the one the game console and viewer contract talk
to - and `WorldServer.advance_tick` ran hourly aging plus a daily health
roll and stopped. Grepping `wiring.py` for `upkeep`, `head_tax`,
`accrue_wages`, `pension` or `payroll` returned nothing at all. Living
was free there, existing was untaxed, no wage promise ever came due, no
retiree drew anything, and a resident whose dollar wallet reached zero
was only ever marked dormant if a burn happened to be the thing that
emptied it. That is not a smaller economy than the runner's; it is no
economy, so the two roots were running different physics over the same
genesis file and every stake DESIGN.md section 3 describes was absent
from the server the roadmap is migrating to.

Every test in this file drives `WorldServer.advance_tick()` - the real
tick loop of the real root, booted through the real `build_world_server`
- and never `world/life/day_jobs.py`'s functions directly. That is
deliberate and it is the whole point of the file. Calling the helper is
exactly what the existing tests already did for `run_daily_upkeep_tick`,
`charge_daily_head_tax` and `pay_pension`, all three of which were
complete, covered and invoked by nothing on this root; a test that only
calls the helper stays green when the wiring is deleted. `genesis_tick=
143` is used so the very first `advance_tick()` lands on tick 144, the
first completed game-day (`core/clock.py::is_day_boundary`), instead of
booting a hundred and forty-four ticks to get there.
"""

from __future__ import annotations

import pytest

from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.life.upkeep import UPKEEP_TOTAL_SIM_PER_DAY
from world.tools.models import WagePromise
from world.wiring import PACKAGE_DIR, WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map

# A small town, so a test that has to reach a day boundary boots in
# tenths of a second. The economy under test is per-household and
# per-head; ten residents exercise every branch two hundred and fifty
# would.
SMALL_GENESIS = PACKAGE_DIR / "genesis_10.json"

# A real government rate. The genesis policy is 0 bps (ECON-01), at
# which `charge_daily_head_tax` correctly moves nothing - so a test that
# left the rate alone would pass whether or not the tax ran at all.
TEST_TAX_RATE_BPS = 300


async def boot(genesis_tick: int = 143):
    return await build_world_server(
        WorldConfig(genesis_json_path=SMALL_GENESIS, genesis_tick=genesis_tick)
    )


async def set_tax_rate(server, bps: int) -> None:
    policy = await server.world.charter.store.get_policy()
    await server.world.charter.store.push_policy(policy.model_copy(update={"tax_rate_bps": bps}))


def transactions_by_reason(server, reason: TransactionReason) -> list:
    return [t for t in server.world.ledger.transactions if t.reason is reason]


@pytest.mark.asyncio
async def test_the_day_boundary_charges_every_household_its_upkeep() -> None:
    """Food, shelter and heat, billed to the household, on this root."""
    server = await boot()
    try:
        alive = [a for a in server.world.agents.values() if a.alive]
        before = {a.agent_id: server.world.ledger.sim_balance(a.agent_id) for a in alive}

        result = await server.advance_tick()
        assert result.tick.tick_index == 144

        upkeep = transactions_by_reason(server, TransactionReason.UPKEEP)
        assert upkeep, (
            "ECON-08: a whole game-day passed on the world.main root and "
            "nobody paid for food, shelter or heat"
        )
        # The town's whole bill, derived from upkeep.py's own per-head
        # total rather than retyped, so a re-rate moves this with it.
        assert sum(t.amount for t in upkeep) == len(alive) * UPKEEP_TOTAL_SIM_PER_DAY
        assert any(
            server.world.ledger.sim_balance(agent_id) < balance
            for agent_id, balance in before.items()
        )
        assert server.last_day_jobs is not None
        assert server.last_day_jobs.upkeep_households == server.last_day_jobs.households
        assert server.last_day_jobs.upkeep_failed == 0
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_the_day_boundary_charges_the_head_tax_at_the_governments_rate() -> None:
    """A per-capita levy on every head, counted in sim and not in calls.

    ECON-01's lesson applies to the wiring as much as to the gate: a
    head-tax job that visited every household and moved no money looked
    identical to one that never ran. This asserts the money.
    """
    server = await boot()
    try:
        await set_tax_rate(server, TEST_TAX_RATE_BPS)
        heads = sum(1 for a in server.world.agents.values() if a.alive)
        treasury_before = server.world.ledger.sim_balance(TREASURY_ACCOUNT_ID)

        await server.advance_tick()

        tax = transactions_by_reason(server, TransactionReason.TAX)
        assert tax, "ECON-08: nobody on this root has ever paid for existing"
        assert len(tax) == heads, "the levy is per head, including children and the retired"
        assert server.world.ledger.sim_balance(TREASURY_ACCOUNT_ID) > treasury_before
        assert server.last_day_jobs is not None
        assert server.last_day_jobs.head_tax_rate_bps == TEST_TAX_RATE_BPS
        assert server.last_day_jobs.head_tax_sim > 0
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_wage_promise_comes_due_and_the_overdue_notice_reaches_the_bus() -> None:
    """The world never pays. It counts - and it says so to both sides."""
    server = await boot()
    try:
        business_id, business = next(iter(server.world.businesses.items()))
        employee_id = next(
            agent_id
            for agent_id, agent in server.world.agents.items()
            if agent.alive and agent_id != business.owner_agent_id
        )
        server.world.wage_promises[f"{business_id}|{employee_id}"] = WagePromise(
            business_id=business_id, employee_id=employee_id, wage_sim=120, promised_tick=0
        )

        overdue: list[WorldEvent] = []

        async def collect(event: WorldEvent) -> None:
            if event.type == "business.wage.overdue":
                overdue.append(event)

        server.event_bus.subscribe("*", collect)
        await server.advance_tick()

        promise = server.world.wage_promises[f"{business_id}|{employee_id}"]
        assert promise.accrued_sim == 120, (
            "ECON-08: accrue_wages has no caller on this root, so a day's "
            "work was never even counted as owed"
        )
        assert overdue and overdue[0].payload["employee_id"] == employee_id
        assert overdue[0].payload["owed_sim"] == 120
        assert employee_id in overdue[0].payload["involved_agent_ids"]
        assert server.last_day_jobs is not None
        assert server.last_day_jobs.wages_accrued == 1
        assert server.last_day_jobs.wages_overdue == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_retiree_with_a_salary_history_draws_a_pension() -> None:
    """DESIGN.md section 4: the treasury pays at 70+.

    The lookup is the finding inside the finding. `WorldState.
    wage_promises` is keyed `"{business_id}|{employee_id}"`, so reading
    it by bare agent id can never hit and every retiree in the town
    answers "no salary to take a quarter of" - a pension job that looks
    fully wired and pays nobody, for ever.
    """
    server = await boot()
    try:
        business_id, business = next(iter(server.world.businesses.items()))
        retiree_id = next(
            agent_id
            for agent_id, agent in server.world.agents.items()
            if agent.alive and agent_id != business.owner_agent_id
        )
        server.world.agents[retiree_id] = server.world.agents[retiree_id].model_copy(
            update={"age_years": 71.0}
        )
        server.world.wage_promises[f"{business_id}|{retiree_id}"] = WagePromise(
            business_id=business_id, employee_id=retiree_id, wage_sim=400, promised_tick=0
        )

        await server.advance_tick()

        pensions = transactions_by_reason(server, TransactionReason.PENSION)
        assert pensions, "ECON-08/ECON-09: growing old on this root is pure loss"
        paid = [t for t in pensions if t.to_account == retiree_id]
        assert paid and paid[0].amount == 100, "a quarter of the last salary of 400"
        assert server.last_day_jobs is not None
        assert server.last_day_jobs.pensions_paid == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_an_empty_wallet_falls_dormant_on_the_very_next_tick() -> None:
    """Cognitive poverty has to be reachable by more than one route.

    `burn_inference` marks dormancy when the burn itself is what emptied
    the wallet. Nothing caught a wallet emptied any other way, so
    `check_dormancy_death` - which this root's hourly block has been
    running all along - could never return True, `revive` had nobody to
    wake, and the whole chain from running out of money to dying of it
    was unreachable. Asserted off a NON-day-boundary tick: the sweep is
    every tick, because a wallet that empties at 09:10 must not buy
    thinking until midnight.
    """
    server = await boot(genesis_tick=10)
    try:
        broke_id = next(
            agent_id for agent_id, agent in server.world.agents.items() if agent.alive
        )
        await server.world.ledger.move(
            from_account_id=broke_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=server.world.ledger.usd_balance(broke_id),
            currency=Currency.USD,
            reason=TransactionReason.TRANSFER,
            tick=10,
            memo="spent it all",
        )
        assert not server.world.agents[broke_id].dormancy.is_dormant

        result = await server.advance_tick()
        assert result.tick.tick_index % 144 != 0, "this must not be a day boundary"

        assert server.world.agents[broke_id].dormancy.is_dormant, (
            "ECON-08: the zero-wallet sweep has no caller on this root"
        )
        assert server.world.agents[broke_id].dormancy.dormant_since_hours is not None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_solvent_town_is_left_alone_by_the_sweep() -> None:
    """The sweep must not mark the whole cast dormant on tick one.

    The obvious wrong implementation reads each resident's OWN wallet
    and answers "$0" for anybody the ledger cannot price - a minor
    billed to a parent, an account that does not exist - which would
    take the entire town dormant before it had done anything.
    """
    server = await boot(genesis_tick=10)
    try:
        for _ in range(3):
            await server.advance_tick()
        assert not any(a.dormancy.is_dormant for a in server.world.agents.values())
    finally:
        await server.close()
