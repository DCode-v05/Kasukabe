"""Tests for world.life.birth: submit -> god queue -> grant/deny."""

from __future__ import annotations

import random

import pytest

from world.life.birth import (
    BirthQueueHeldError,
    deny_birth_request,
    grant_birth_request,
    submit_birth_request,
)
from world.life.clock import GameClock
from world.life.models import AgentLifeRecord, BirthRequestStatus, Household, PopulationDials, Sex
from world.life.ports import InsufficientFundsError
from world.life.tests.conftest import InMemoryEconomy


def make_parent(agent_id: str) -> AgentLifeRecord:
    return AgentLifeRecord(agent_id=agent_id, age_years=30.0, sex=Sex.FEMALE, household_id="h001")


def test_submit_birth_request_requires_sponsor_to_be_a_parent() -> None:
    with pytest.raises(ValueError):
        submit_birth_request("r1", "h001", ["p1", "p2"], "p3", GameClock())


def test_submit_birth_request_creates_pending_request() -> None:
    request, events = submit_birth_request(
        "r1", "h001", ["p1", "p2"], "p1", GameClock(total_hours=5.0)
    )

    assert request.status is BirthRequestStatus.PENDING
    assert request.requested_at_hours == 5.0
    assert events[0].type == "agent.birth.requested"


@pytest.mark.asyncio
async def test_grant_birth_request_charges_blessing_fee_and_spawns_newborn(
    economy: InMemoryEconomy,
) -> None:
    economy.sim_balances["p1"] = 60_000
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    household = Household(household_id="h001", member_ids=["p1", "p2"])
    parents = [make_parent("p1"), make_parent("p2")]

    outcome = await grant_birth_request(
        request,
        "baby1",
        household,
        parents,
        current_population=250,
        dials=PopulationDials(),
        economy=economy,
        rng=random.Random(0),
        at=GameClock(total_hours=10.0),
    )

    assert outcome.newborn.agent_id == "baby1"
    assert outcome.newborn.age_years == 0.0
    assert outcome.newborn.household_id == "h001"
    assert outcome.newborn.parent_ids == ["p1", "p2"]
    assert outcome.newborn.sponsor_parent_id == "p1"
    assert outcome.request.status is BirthRequestStatus.GRANTED
    assert outcome.request.newborn_agent_id == "baby1"
    assert "baby1" in outcome.household.member_ids
    assert all("baby1" in parent.children_ids for parent in outcome.parents)
    assert economy.debits == [("p1", 5_000, "baby_blessing")]
    event_types = [e.type for e in outcome.events]
    assert "agent.birth.granted" in event_types
    assert "agent.spawned" in event_types


@pytest.mark.asyncio
async def test_grant_birth_request_raises_when_population_at_ceiling(
    economy: InMemoryEconomy,
) -> None:
    economy.sim_balances["p1"] = 60_000
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    household = Household(household_id="h001", member_ids=["p1", "p2"])
    parents = [make_parent("p1"), make_parent("p2")]
    dials = PopulationDials(target_band_max=250)

    with pytest.raises(BirthQueueHeldError):
        await grant_birth_request(
            request,
            "baby1",
            household,
            parents,
            current_population=250,
            dials=dials,
            economy=economy,
            rng=random.Random(0),
            at=GameClock(),
        )
    assert economy.debits == []


@pytest.mark.asyncio
async def test_grant_birth_request_raises_when_throttle_is_zero(economy: InMemoryEconomy) -> None:
    economy.sim_balances["p1"] = 60_000
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    household = Household(household_id="h001", member_ids=["p1", "p2"])
    parents = [make_parent("p1"), make_parent("p2")]
    dials = PopulationDials(blessing_throttle=0.0, target_band_max=1000)

    with pytest.raises(BirthQueueHeldError):
        await grant_birth_request(
            request,
            "baby1",
            household,
            parents,
            current_population=10,
            dials=dials,
            economy=economy,
            rng=random.Random(0),
            at=GameClock(),
        )


@pytest.mark.asyncio
async def test_grant_birth_request_raises_when_sponsor_cannot_afford_blessing(
    economy: InMemoryEconomy,
) -> None:
    economy.sim_balances["p1"] = 100
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    household = Household(household_id="h001", member_ids=["p1", "p2"])
    parents = [make_parent("p1"), make_parent("p2")]

    with pytest.raises(InsufficientFundsError):
        await grant_birth_request(
            request,
            "baby1",
            household,
            parents,
            current_population=250,
            dials=PopulationDials(),
            economy=economy,
            rng=random.Random(0),
            at=GameClock(),
        )


@pytest.mark.asyncio
async def test_grant_birth_request_rejects_non_pending_request(economy: InMemoryEconomy) -> None:
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    denied, _ = deny_birth_request(request, GameClock())
    household = Household(household_id="h001", member_ids=["p1", "p2"])
    parents = [make_parent("p1"), make_parent("p2")]

    with pytest.raises(ValueError):
        await grant_birth_request(
            denied,
            "baby1",
            household,
            parents,
            current_population=250,
            dials=PopulationDials(),
            economy=economy,
            rng=random.Random(0),
            at=GameClock(),
        )


def test_deny_birth_request_marks_denied() -> None:
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    denied, events = deny_birth_request(request, GameClock(), silent=True)

    assert denied.status is BirthRequestStatus.DENIED
    assert denied.silent_deny is True
    assert events[0].type == "agent.birth.denied"


def test_deny_birth_request_rejects_non_pending_request() -> None:
    request, _ = submit_birth_request("r1", "h001", ["p1", "p2"], "p1", GameClock())
    denied, _ = deny_birth_request(request, GameClock())

    with pytest.raises(ValueError):
        deny_birth_request(denied, GameClock())
