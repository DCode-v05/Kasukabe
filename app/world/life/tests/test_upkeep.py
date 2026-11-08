"""Tests for world.life.upkeep: daily household upkeep, dependents, and
the escalating hunger -> ailment -> dormancy -> death consequence.

The last test in this file (test_simulation_...) is the task's required
proof: a multi-game-day run against the REAL world.ledger.core.Ledger
(not the InMemoryEconomy fake every other test here uses) showing an
earning household stays solvent, a dependents-only household starves on
schedule and dies through the EXISTING dormancy/death path
(world.life.death.check_dormancy_death + world.life.tick.
run_hourly_life_tick, both imported unmodified), and the ledger's $1000
USD invariant survives the whole run. Per this project's governing
lesson ("never trust a mocked seam; prove against the real thing"), the
one test that matters most for the ledger-invariant claim deliberately
does not use the fake.
"""

from __future__ import annotations

import random

import pytest

from world.ledger.core import Ledger
from world.ledger.errors import InsufficientFundsError as LedgerInsufficientFundsError
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import PENSION_MINIMUM_AGE, TOTAL_USD_CENTS, TREASURY_ACCOUNT_ID
from world.life.clock import GameClock
from world.life.death import check_dormancy_death
from world.life.health import work_capacity as health_work_capacity
from world.life.models import (
    Ailment,
    AilmentKind,
    AgentLifeRecord,
    DeathCause,
    HealthState,
    PopulationDials,
    Sex,
)
from world.life.ports import InsufficientFundsError
from world.life.tests.conftest import InMemoryEconomy
from world.life.tick import run_hourly_life_tick
from world.life.upkeep import (
    HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD,
    HUNGER_MILD_WORK_CAPACITY_MULTIPLIER,
    HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD,
    UPKEEP_TOTAL_SIM_PER_DAY,
    HungerState,
    advance_hunger,
    collect_household_upkeep,
    compute_household_bill,
    earner_burden,
    effective_work_capacity,
    is_dependent,
    per_agent_upkeep,
    run_daily_upkeep_tick,
    summarize_household_upkeep,
)


def make_agent(**overrides: object) -> AgentLifeRecord:
    base: dict[str, object] = {"agent_id": "a1", "age_years": 30.0, "sex": Sex.MALE}
    base.update(overrides)
    return AgentLifeRecord(**base)  # type: ignore[arg-type]


def test_is_dependent_true_for_minor() -> None:
    assert is_dependent(make_agent(age_years=10.0)) is True


def test_is_dependent_true_at_pension_minimum_age() -> None:
    assert is_dependent(make_agent(age_years=float(PENSION_MINIMUM_AGE))) is True


def test_is_dependent_false_for_working_age_adult() -> None:
    assert is_dependent(make_agent(age_years=30.0)) is False


def test_is_dependent_false_just_under_pension_age() -> None:
    assert is_dependent(make_agent(age_years=float(PENSION_MINIMUM_AGE) - 0.1)) is False


def test_per_agent_upkeep_uniform_regardless_of_dependent_status() -> None:
    earner = per_agent_upkeep(make_agent(agent_id="earner", age_years=30.0))
    dependent = per_agent_upkeep(make_agent(agent_id="minor", age_years=5.0))

    assert earner.total_sim == dependent.total_sim == UPKEEP_TOTAL_SIM_PER_DAY
    assert earner.is_dependent is False
    assert dependent.is_dependent is True


def test_compute_household_bill_splits_earners_and_dependents() -> None:
    earner = make_agent(agent_id="earner", age_years=30.0)
    minor = make_agent(agent_id="minor", age_years=8.0)
    retired = make_agent(agent_id="retired", age_years=75.0)

    bill = compute_household_bill("h1", day=0, members=[earner, minor, retired])

    assert bill.total_sim == 3 * UPKEEP_TOTAL_SIM_PER_DAY
    assert bill.earner_ids == ["earner"]
    assert set(bill.dependent_ids) == {"minor", "retired"}
    assert bill.dependents_total_sim == 2 * UPKEEP_TOTAL_SIM_PER_DAY
    assert bill.earners_total_sim == UPKEEP_TOTAL_SIM_PER_DAY


def test_compute_household_bill_excludes_dead_members() -> None:
    alive = make_agent(agent_id="alive", age_years=30.0)
    dead = make_agent(agent_id="dead", age_years=30.0, alive=False, death_cause=DeathCause.TERMINAL)

    bill = compute_household_bill("h1", day=0, members=[alive, dead])

    assert bill.total_sim == UPKEEP_TOTAL_SIM_PER_DAY
    assert bill.earner_ids == ["alive"]


def test_compute_household_bill_zero_when_no_alive_members() -> None:
    dead = make_agent(alive=False, death_cause=DeathCause.TERMINAL)
    bill = compute_household_bill("h1", day=0, members=[dead])
    assert bill.total_sim == 0
    assert bill.breakdowns == []


def test_earner_burden_splits_evenly_across_earners() -> None:
    e1 = make_agent(agent_id="e1", age_years=30.0)
    e2 = make_agent(agent_id="e2", age_years=40.0)
    minor = make_agent(agent_id="minor", age_years=8.0)
    bill = compute_household_bill("h1", day=0, members=[e1, e2, minor])

    burden = earner_burden(bill)

    assert set(burden) == {"e1", "e2"}
    assert sum(burden.values()) == bill.total_sim


def test_earner_burden_remainder_goes_to_first_payers_in_list_order() -> None:
    e1 = make_agent(agent_id="e1", age_years=30.0)
    e2 = make_agent(agent_id="e2", age_years=40.0)
    e3 = make_agent(agent_id="e3", age_years=50.0)
    minor = make_agent(agent_id="minor", age_years=8.0)
    bill = compute_household_bill("h1", day=0, members=[e1, e2, e3, minor])

    burden = earner_burden(bill)

    assert sum(burden.values()) == bill.total_sim
    values = sorted(burden.values())
    assert values[-1] - values[0] <= 1


def test_earner_burden_falls_back_to_dependents_when_no_earners() -> None:
    minor = make_agent(agent_id="minor", age_years=8.0)
    retired = make_agent(agent_id="retired", age_years=75.0)
    bill = compute_household_bill("h1", day=0, members=[minor, retired])

    burden = earner_burden(bill)

    assert set(burden) == {"minor", "retired"}
    assert sum(burden.values()) == bill.total_sim


def test_earner_burden_empty_when_no_alive_members() -> None:
    dead = make_agent(alive=False, death_cause=DeathCause.TERMINAL)
    bill = compute_household_bill("h1", day=0, members=[dead])
    assert earner_burden(bill) == {}


def test_summarize_household_upkeep_bundles_bill_and_burden() -> None:
    earner = make_agent(agent_id="earner", age_years=30.0)
    minor = make_agent(agent_id="minor", age_years=8.0)

    summary = summarize_household_upkeep("h1", day=3, members=[earner, minor])

    assert summary.bill.household_id == "h1"
    assert summary.bill.day == 3
    assert summary.burden_sim == {"earner": summary.bill.total_sim}


@pytest.mark.asyncio
async def test_collect_household_upkeep_pays_in_full_from_single_earner(
    economy: InMemoryEconomy,
) -> None:
    earner = make_agent(agent_id="earner", age_years=30.0)
    minor = make_agent(agent_id="minor", age_years=8.0)
    economy.sim_balances["earner"] = 10_000
    bill = compute_household_bill("h1", day=0, members=[earner, minor])

    outcome = await collect_household_upkeep(bill, economy)

    assert outcome.paid_in_full is True
    assert outcome.shortfall_sim == 0
    assert outcome.amount_paid_sim == bill.total_sim
    assert outcome.contributions == {"earner": bill.total_sim}
    assert economy.sim_balances["earner"] == 10_000 - bill.total_sim
    assert "minor" not in economy.sim_balances or economy.sim_balances["minor"] == 0
    assert economy.debits == [("earner", bill.total_sim, "daily_upkeep")]
    assert outcome.events[0].type == "economy.upkeep.charged"


@pytest.mark.asyncio
async def test_collect_household_upkeep_pools_across_multiple_earners(
    economy: InMemoryEconomy,
) -> None:
    e1 = make_agent(agent_id="e1", age_years=30.0)
    e2 = make_agent(agent_id="e2", age_years=40.0)
    economy.sim_balances["e1"] = 10
    economy.sim_balances["e2"] = 10_000
    bill = compute_household_bill("h1", day=0, members=[e1, e2])

    outcome = await collect_household_upkeep(bill, economy)

    assert outcome.paid_in_full is True
    assert outcome.contributions["e1"] == 10
    assert outcome.contributions["e2"] == bill.total_sim - 10


@pytest.mark.asyncio
async def test_collect_household_upkeep_falls_back_to_dependents_when_no_earners(
    economy: InMemoryEconomy,
) -> None:
    minor = make_agent(agent_id="minor", age_years=8.0)
    retired = make_agent(agent_id="retired", age_years=75.0)
    economy.sim_balances["minor"] = 10_000
    economy.sim_balances["retired"] = 10_000
    bill = compute_household_bill("h1", day=0, members=[minor, retired])

    outcome = await collect_household_upkeep(bill, economy)

    assert outcome.paid_in_full is True
    assert set(outcome.contributions) <= {"minor", "retired"}
    assert sum(outcome.contributions.values()) == bill.total_sim


@pytest.mark.asyncio
async def test_collect_household_upkeep_partial_payment_on_shortfall(
    economy: InMemoryEconomy,
) -> None:
    earner = make_agent(agent_id="earner", age_years=30.0)
    minor = make_agent(agent_id="minor", age_years=8.0)
    economy.sim_balances["earner"] = 5
    bill = compute_household_bill("h1", day=0, members=[earner, minor])

    outcome = await collect_household_upkeep(bill, economy)

    assert outcome.paid_in_full is False
    assert outcome.amount_paid_sim == 5
    assert outcome.shortfall_sim == bill.total_sim - 5
    assert economy.sim_balances["earner"] == 0
    assert outcome.events[0].type == "economy.upkeep.defaulted"


@pytest.mark.asyncio
async def test_collect_household_upkeep_zero_bill_is_trivially_paid(
    economy: InMemoryEconomy,
) -> None:
    dead = make_agent(alive=False, death_cause=DeathCause.TERMINAL)
    bill = compute_household_bill("h1", day=0, members=[dead])

    outcome = await collect_household_upkeep(bill, economy)

    assert outcome.paid_in_full is True
    assert outcome.events == []
    assert economy.debits == []


def test_effective_work_capacity_full_when_well_fed() -> None:
    assert effective_work_capacity(HealthState(), HungerState()) == 1.0


def test_effective_work_capacity_mild_penalty_below_severe_threshold() -> None:
    hunger = HungerState(days_since_meal=1)
    assert effective_work_capacity(HealthState(), hunger) == pytest.approx(
        HUNGER_MILD_WORK_CAPACITY_MULTIPLIER
    )


def test_effective_work_capacity_no_double_penalty_once_ailment_reflects_severity() -> None:
    ailment = Ailment(kind=AilmentKind.SHORT, onset_day=0, duration_days=1)
    health = HealthState(current_ailment=ailment)
    hunger = HungerState(consecutive_unpaid_days=HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD)

    assert effective_work_capacity(health, hunger) == health_work_capacity(health)


def test_advance_hunger_noop_for_dead_agent() -> None:
    agent = make_agent(alive=False, death_cause=DeathCause.TERMINAL)
    updated, hunger, events = advance_hunger(
        random.Random(0), agent, HungerState(), day=0, paid_in_full=False
    )
    assert updated is agent
    assert hunger.consecutive_unpaid_days == 0
    assert events == []


def test_advance_hunger_paid_in_full_keeps_streak_at_zero_with_no_events() -> None:
    agent = make_agent()
    updated, hunger, events = advance_hunger(
        random.Random(0), agent, HungerState(), day=0, paid_in_full=True
    )
    assert hunger.consecutive_unpaid_days == 0
    assert events == []
    assert updated is agent


def test_advance_hunger_paid_after_hungry_resets_and_emits_relief() -> None:
    agent = make_agent()
    hungry = HungerState(consecutive_unpaid_days=1, days_since_meal=1)
    updated, hunger, events = advance_hunger(
        random.Random(0), agent, hungry, day=5, paid_in_full=True
    )
    assert hunger.consecutive_unpaid_days == 0
    assert events[0].type == "health.hunger.relieved"
    assert updated is agent


def test_advance_hunger_first_unpaid_day_is_mild_with_no_ailment_or_dormancy() -> None:
    agent = make_agent()
    updated, hunger, events = advance_hunger(
        random.Random(0), agent, HungerState(), day=0, paid_in_full=False
    )
    assert hunger.consecutive_unpaid_days == 1
    assert updated.health.current_ailment is None
    assert updated.dormancy.is_dormant is False
    assert [e.type for e in events] == ["health.hunger.worsened"]


def test_advance_hunger_onsets_malnutrition_ailment_at_severe_threshold() -> None:
    agent = make_agent()
    hunger = HungerState(
        consecutive_unpaid_days=HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD - 1,
        days_since_meal=HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD - 1,
    )

    updated, new_hunger, events = advance_hunger(
        random.Random(0), agent, hunger, day=1, paid_in_full=False
    )

    assert new_hunger.consecutive_unpaid_days == HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD
    assert updated.health.current_ailment is not None
    assert updated.health.current_ailment.kind is AilmentKind.SHORT
    assert updated.dormancy.is_dormant is False
    event_types = [e.type for e in events]
    assert "health.ailment.onset" in event_types


def test_advance_hunger_does_not_stack_a_second_ailment_if_already_sick() -> None:
    existing = Ailment(kind=AilmentKind.MEDIUM, onset_day=0, duration_days=5)
    agent = make_agent(health=HealthState(current_ailment=existing))
    hunger = HungerState(
        consecutive_unpaid_days=HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD - 1,
        days_since_meal=HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD - 1,
    )

    updated, _, events = advance_hunger(random.Random(0), agent, hunger, day=1, paid_in_full=False)

    assert updated.health.current_ailment is existing
    assert all(e.type != "health.ailment.onset" for e in events)


def test_advance_hunger_marks_dormant_at_dormancy_threshold() -> None:
    agent = make_agent()
    hunger = HungerState(
        consecutive_unpaid_days=HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD - 1,
        days_since_meal=HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD - 1,
    )

    updated, new_hunger, events = advance_hunger(
        random.Random(0), agent, hunger, day=2, paid_in_full=False
    )

    assert new_hunger.consecutive_unpaid_days == HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD
    assert updated.dormancy.is_dormant is True
    assert updated.dormancy.dormant_since_hours == GameClock.at_day(2).total_hours
    assert any(e.type == "health.agent.dormant" for e in events)


def test_advance_hunger_dormancy_is_idempotent_once_already_dormant() -> None:
    agent = make_agent()
    agent, hunger, _ = advance_hunger(
        random.Random(0),
        agent,
        HungerState(
            consecutive_unpaid_days=HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD - 1,
            days_since_meal=HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD - 1,
        ),
        day=2,
        paid_in_full=False,
    )
    assert agent.dormancy.dormant_since_hours == GameClock.at_day(2).total_hours

    agent, hunger, _ = advance_hunger(random.Random(0), agent, hunger, day=3, paid_in_full=False)

    assert agent.dormancy.dormant_since_hours == GameClock.at_day(2).total_hours


@pytest.mark.asyncio
async def test_run_daily_upkeep_tick_pays_and_updates_members(economy: InMemoryEconomy) -> None:
    earner = make_agent(agent_id="earner", age_years=30.0)
    minor = make_agent(agent_id="minor", age_years=8.0)
    economy.sim_balances["earner"] = 10_000

    result = await run_daily_upkeep_tick(
        random.Random(0), "h1", 0, [earner, minor], {}, economy, PopulationDials()
    )

    assert result.outcome.paid_in_full is True
    assert result.updated_hunger["earner"].consecutive_unpaid_days == 0
    assert result.updated_hunger["minor"].consecutive_unpaid_days == 0
    ids = [m.agent_id for m in result.updated_members]
    assert ids == ["earner", "minor"]


@pytest.mark.asyncio
async def test_run_daily_upkeep_tick_skips_dead_members(economy: InMemoryEconomy) -> None:
    dead = make_agent(agent_id="dead", alive=False, death_cause=DeathCause.TERMINAL)

    result = await run_daily_upkeep_tick(
        random.Random(0), "h1", 0, [dead], {}, economy, PopulationDials()
    )

    assert result.outcome.bill.total_sim == 0
    assert result.updated_members[0] is dead
    assert result.updated_hunger == {}


@pytest.mark.asyncio
async def test_run_daily_upkeep_tick_household_starves_together(
    economy: InMemoryEconomy,
) -> None:
    """DESIGN.md section 3: a household with no earner "starves together" -
    every alive member's hunger streak advances the same day, not just
    whichever one the caller happened to list first.
    """
    minor = make_agent(agent_id="minor", age_years=8.0)
    retired = make_agent(agent_id="retired", age_years=75.0)

    result = await run_daily_upkeep_tick(
        random.Random(0), "h1", 0, [minor, retired], {}, economy, PopulationDials()
    )

    assert result.outcome.paid_in_full is False
    assert result.updated_hunger["minor"].consecutive_unpaid_days == 1
    assert result.updated_hunger["retired"].consecutive_unpaid_days == 1


class LedgerEconomyAdapter:
    """Adapts world.ledger.core.Ledger to the EconomyPort protocol.

    Test-only: the real adapter belongs to whichever module wires
    world's tick loop to the ledger once that seam is built (see
    ports.py's module docstring - "it should collapse to a direct call
    into ledger.py once that module lands"). Not exported, not part of
    upkeep.py's public surface.

    Upkeep sim lands in the treasury (TransactionReason.TRANSFER - the
    ledger has no dedicated UPKEEP reason yet, see upkeep.py's
    UPKEEP_TRANSACTION_REASON comment), the same way every other sim fee
    in ledger/core.py already flows to TREASURY_ACCOUNT_ID
    (collect_tax, register_business, exchange_sim_for_usd).
    """

    def __init__(self, ledger: Ledger, tick: int) -> None:
        self.ledger = ledger
        self.tick = tick

    async def debit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        try:
            await self.ledger.move(
                from_account_id=agent_id,
                to_account_id=TREASURY_ACCOUNT_ID,
                amount=amount,
                currency=Currency.SIM,
                reason=TransactionReason.TRANSFER,
                tick=self.tick,
                memo=reason,
            )
        except LedgerInsufficientFundsError as exc:
            raise InsufficientFundsError(str(exc)) from exc

    async def credit_sim(self, agent_id: str, amount: int, reason: str) -> None:
        await self.ledger.move(
            from_account_id=TREASURY_ACCOUNT_ID,
            to_account_id=agent_id,
            amount=amount,
            currency=Currency.SIM,
            reason=TransactionReason.WAGE,
            tick=self.tick,
            memo=reason,
        )

    async def credit_usd_cents(self, agent_id: str, amount_cents: int, reason: str) -> None:
        raise NotImplementedError("not exercised by upkeep")

    async def get_sim_balance(self, agent_id: str) -> int:
        return self.ledger.sim_balance(agent_id)


@pytest.mark.asyncio
async def test_simulation_earning_household_solvent_dependents_only_household_starves() -> None:
    ledger = Ledger()

    earner = make_agent(agent_id="earner", age_years=30.0, household_id="solvent")
    earner_dependent = make_agent(
        agent_id="earner_dependent", age_years=8.0, household_id="solvent"
    )
    starved_minor = make_agent(agent_id="starved_minor", age_years=8.0, household_id="starving")
    starved_elder = make_agent(
        agent_id="starved_elder", age_years=75.0, household_id="starving"
    )
    all_agent_ids = ["earner", "earner_dependent", "starved_minor", "starved_elder"]

    await ledger.genesis(
        all_agent_ids, agent_usd_cents=0, treasury_usd_cents=TOTAL_USD_CENTS, tick=0
    )
    ledger.check_invariant()

    await ledger.mint_sim("starved_minor", 50, tick=0, reason=TransactionReason.GENESIS)
    await ledger.mint_sim("starved_elder", 50, tick=0, reason=TransactionReason.GENESIS)
    await ledger.mint_sim(TREASURY_ACCOUNT_ID, 10_000, tick=0, reason=TransactionReason.GENESIS)

    dials = PopulationDials()
    hunger: dict[str, HungerState] = {}
    solvent_members = [earner, earner_dependent]
    starving_members = [starved_minor, starved_elder]

    daily_wage = UPKEEP_TOTAL_SIM_PER_DAY * 2 + 20

    for day in range(4):
        economy = LedgerEconomyAdapter(ledger, tick=day)

        await ledger.pay_wage(TREASURY_ACCOUNT_ID, "earner", daily_wage, tick=day)

        solvent_result = await run_daily_upkeep_tick(
            random.Random(day), "solvent", day, solvent_members, hunger, economy, dials
        )
        solvent_members = solvent_result.updated_members
        hunger.update(solvent_result.updated_hunger)
        assert solvent_result.outcome.paid_in_full is True, (
            f"solvent household defaulted on day {day}"
        )

        starving_result = await run_daily_upkeep_tick(
            random.Random(day), "starving", day, starving_members, hunger, economy, dials
        )
        starving_members = starving_result.updated_members
        hunger.update(starving_result.updated_hunger)

        ledger.check_invariant()

    for member in solvent_members:
        assert member.alive is True
        assert member.dormancy.is_dormant is False
        assert member.health.current_ailment is None
        assert hunger[member.agent_id].consecutive_unpaid_days == 0

    for member in starving_members:
        assert member.alive is True
        assert member.dormancy.is_dormant is True
        assert member.health.current_ailment is not None
        assert member.health.current_ailment.kind is AilmentKind.SHORT

    dormant_since = starving_members[0].dormancy.dormant_since_hours
    assert dormant_since == GameClock.at_day(HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD - 1).total_hours

    death_hour = dormant_since + 24.0
    now = GameClock(total_hours=death_hour)
    assert check_dormancy_death(random.Random(0), starving_members[0].dormancy, now, dials) is True

    for i, member in enumerate(starving_members):
        updated, events = await run_hourly_life_tick(
            random.Random(0), member, now=now, elapsed_hours=1.0, dials=dials
        )
        starving_members[i] = updated
        assert updated.alive is False
        assert updated.death_cause is DeathCause.USD_ZERO_UNREVIVED
        assert any(e.type == "agent.died" for e in events)

    assert ledger.halted is False
    ledger.check_invariant()
    assert ledger.usd_total() == TOTAL_USD_CENTS
