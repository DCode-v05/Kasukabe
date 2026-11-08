"""Tests for world.life.household: marriage and household merging."""

from __future__ import annotations

import pytest

from world.life.clock import GameClock
from world.life.household import MarriageIneligibleError, marry, merge_households
from world.life.models import AgentLifeRecord, Household, MaritalStatus, Sex
from world.life.ports import InsufficientFundsError
from world.life.tests.conftest import InMemoryEconomy


def make_agent(agent_id: str, age_years: float = 25.0, **overrides: object) -> AgentLifeRecord:
    return AgentLifeRecord(agent_id=agent_id, age_years=age_years, sex=Sex.MALE, **overrides)  # type: ignore[arg-type]


def test_merge_households_creates_new_when_neither_exists() -> None:
    household, dissolved = merge_households(None, None, ["a1", "a2"], "h001", at_hours=0.0)

    assert household.household_id == "h001"
    assert household.member_ids == ["a1", "a2"]
    assert dissolved is None


def test_merge_households_absorbs_new_members_into_existing_one() -> None:
    existing = Household(household_id="h001", member_ids=["a1"])
    household, dissolved = merge_households(existing, None, ["a1", "a2"], "unused", at_hours=0.0)

    assert household.household_id == "h001"
    assert household.member_ids == ["a1", "a2"]
    assert dissolved is None


def test_merge_households_combines_two_existing_households_and_dissolves_one() -> None:
    household_a = Household(household_id="h001", member_ids=["a1", "a3"])
    household_b = Household(household_id="h002", member_ids=["a2", "a4"])
    household, dissolved = merge_households(
        household_a, household_b, ["a1", "a2"], "unused", at_hours=0.0
    )

    assert household.household_id == "h001"
    assert set(household.member_ids) == {"a1", "a2", "a3", "a4"}
    assert dissolved == "h002"


@pytest.mark.asyncio
async def test_marry_charges_temple_fee_and_links_spouses(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 5_000
    agent_a, agent_b = make_agent("a1"), make_agent("a2")

    outcome = await marry(agent_a, agent_b, None, None, "h001", GameClock(total_hours=0.0), economy)

    assert outcome.agent_a.marital_status is MaritalStatus.MARRIED
    assert outcome.agent_a.spouse_id == "a2"
    assert outcome.agent_b.spouse_id == "a1"
    assert outcome.agent_a.household_id == outcome.agent_b.household_id == "h001"
    assert economy.debits == [("a1", 1_000, "temple_wedding_fee")]
    assert outcome.marriage_record.temple_fee_sim == 1_000
    assert outcome.events[0].type == "agent.marriage.recorded"


@pytest.mark.asyncio
async def test_marry_merges_existing_households(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 5_000
    household_a = Household(household_id="h_a", member_ids=["a1"])
    household_b = Household(household_id="h_b", member_ids=["a2"])
    agent_a, agent_b = make_agent("a1"), make_agent("a2")

    outcome = await marry(
        agent_a, agent_b, household_a, household_b, "unused", GameClock(), economy
    )

    assert outcome.household.household_id == "h_a"
    assert set(outcome.household.member_ids) == {"a1", "a2"}
    assert outcome.dissolved_household_id == "h_b"


@pytest.mark.asyncio
async def test_marry_rejects_minors(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 5_000
    agent_a, agent_b = make_agent("a1"), make_agent("a2", age_years=16.0)

    with pytest.raises(MarriageIneligibleError):
        await marry(agent_a, agent_b, None, None, "h001", GameClock(), economy)


@pytest.mark.asyncio
async def test_marry_rejects_already_married_agent(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 5_000
    agent_a = make_agent("a1", marital_status=MaritalStatus.MARRIED, spouse_id="a9")
    agent_b = make_agent("a2")

    with pytest.raises(MarriageIneligibleError):
        await marry(agent_a, agent_b, None, None, "h001", GameClock(), economy)


@pytest.mark.asyncio
async def test_marry_rejects_marrying_oneself(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 5_000
    agent_a = make_agent("a1")

    with pytest.raises(MarriageIneligibleError):
        await marry(agent_a, agent_a, None, None, "h001", GameClock(), economy)


@pytest.mark.asyncio
async def test_marry_raises_when_fee_payer_cannot_afford_it(economy: InMemoryEconomy) -> None:
    economy.sim_balances["a1"] = 10
    agent_a, agent_b = make_agent("a1"), make_agent("a2")

    with pytest.raises(InsufficientFundsError):
        await marry(agent_a, agent_b, None, None, "h001", GameClock(), economy)
