"""come_of_age: The 250 section 26's life-cycle transition.

Covers the half aging.py could not do - the wallet stops being the
parent's, and the household actually changes - plus the three futures
section 26 names and the preconditions that keep an impossible one out
of the household records.
"""

from __future__ import annotations

import pytest

from world.life.clock import GameClock
from world.life.constants import MINOR_AGE_LIMIT_YEARS, TEMPLE_WEDDING_FEE_SIM
from world.life.household import (
    ComingOfAgeChoice,
    ComingOfAgeError,
    come_of_age,
    leave_household,
)
from world.life.models import AgentLifeRecord, Household, MaritalStatus, Sex

AT = GameClock(total_hours=100.0)


def child(agent_id: str = "kid", *, age: float = MINOR_AGE_LIMIT_YEARS, household: str | None = "hh-parents") -> AgentLifeRecord:
    return AgentLifeRecord(
        agent_id=agent_id,
        age_years=age,
        sex=Sex.FEMALE,
        household_id=household,
        parent_ids=["mum", "dad"],
        sponsor_parent_id="mum",
    )


def family(*members: str) -> Household:
    return Household(household_id="hh-parents", member_ids=list(members), formed_at_hours=0.0)


@pytest.mark.asyncio
async def test_parent_billing_ends_on_every_choice(guardianship):
    """The one thing section 26 makes non-optional, whatever they choose."""
    outcome = await come_of_age(
        child(),
        family("mum", "dad", "kid"),
        ComingOfAgeChoice.STAY_AND_CONTRIBUTE,
        at=AT,
        guardianship=guardianship,
    )
    assert guardianship.ended == ["kid"]
    assert outcome.agent.sponsor_parent_id is None
    ended = [e for e in outcome.events if e.type == "agent.parent_support.ended"]
    assert len(ended) == 1
    assert ended[0].payload["former_sponsor_id"] == "mum"


@pytest.mark.asyncio
async def test_stay_and_contribute_keeps_the_household(guardianship):
    hh = family("mum", "dad", "kid")
    outcome = await come_of_age(
        child(), hh, ComingOfAgeChoice.STAY_AND_CONTRIBUTE, at=AT, guardianship=guardianship
    )
    assert outcome.household.household_id == "hh-parents"
    assert set(outcome.household.member_ids) == {"mum", "dad", "kid"}
    assert outcome.agent.household_id == "hh-parents"
    assert any(e.type == "agent.household.contributor_added" for e in outcome.events)


@pytest.mark.asyncio
async def test_move_out_alone_forms_a_single_person_household(guardianship):
    outcome = await come_of_age(
        child(),
        family("mum", "dad", "kid"),
        ComingOfAgeChoice.MOVE_OUT_ALONE,
        at=AT,
        guardianship=guardianship,
        new_household_id="hh-kid",
    )
    assert outcome.household.member_ids == ["kid"]
    assert outcome.household.formed_at_hours == AT.total_hours
    assert outcome.agent.household_id == "hh-kid"
    assert outcome.childhood_household is not None
    assert set(outcome.childhood_household.member_ids) == {"mum", "dad"}
    formed = [e for e in outcome.events if e.type == "agent.household.formed"]
    assert formed[0].payload["archetype"] == "single_no_dependents"
    assert formed[0].payload["left_household_id"] == "hh-parents"


@pytest.mark.asyncio
async def test_pair_off_marries_and_charges_the_temple(guardianship, economy):
    economy.sim_balances["kid"] = TEMPLE_WEDDING_FEE_SIM
    partner = AgentLifeRecord(
        agent_id="neighbour-kid",
        age_years=MINOR_AGE_LIMIT_YEARS,
        sex=Sex.MALE,
        household_id=None,
    )
    outcome = await come_of_age(
        child(),
        family("mum", "dad", "kid"),
        ComingOfAgeChoice.PAIR_OFF,
        at=AT,
        guardianship=guardianship,
        new_household_id="hh-newlyweds",
        partner=partner,
        economy=economy,
    )
    assert outcome.agent.marital_status is MaritalStatus.MARRIED
    assert outcome.partner is not None and outcome.partner.spouse_id == "kid"
    assert set(outcome.household.member_ids) == {"kid", "neighbour-kid"}
    assert economy.sim_balances["kid"] == 0
    assert outcome.marriage_record is not None
    assert outcome.childhood_household is not None
    assert "kid" not in outcome.childhood_household.member_ids
    formed = [e for e in outcome.events if e.type == "agent.household.formed"]
    assert formed[0].payload["archetype"] == "newlyweds"


@pytest.mark.asyncio
async def test_a_minor_cannot_come_of_age(guardianship):
    with pytest.raises(ComingOfAgeError) as exc:
        await come_of_age(
            child(age=17.9),
            family("mum", "dad", "kid"),
            ComingOfAgeChoice.MOVE_OUT_ALONE,
            at=AT,
            guardianship=guardianship,
            new_household_id="hh-kid",
        )
    assert "under the 18-year threshold" in str(exc.value)
    assert guardianship.ended == []


@pytest.mark.asyncio
async def test_every_missing_precondition_is_reported_at_once(guardianship):
    """Same aggregate-the-reasons discipline as MarriageIneligibleError."""
    with pytest.raises(ComingOfAgeError) as exc:
        await come_of_age(
            child(age=12.0),
            None,
            ComingOfAgeChoice.PAIR_OFF,
            at=AT,
            guardianship=guardianship,
        )
    reasons = exc.value.reasons
    assert len(reasons) == 4
    assert any("under the 18-year threshold" in r for r in reasons)
    assert any("needs a partner" in r for r in reasons)
    assert any("new_household_id" in r for r in reasons)
    assert any("temple fee" in r for r in reasons)


@pytest.mark.asyncio
async def test_staying_needs_a_household_to_stay_in(guardianship):
    with pytest.raises(ComingOfAgeError):
        await come_of_age(
            child(household=None), None, ComingOfAgeChoice.STAY_AND_CONTRIBUTE,
            at=AT, guardianship=guardianship,
        )


def test_leave_household_is_total():
    assert leave_household(None, "kid") is None
    emptied = leave_household(family("kid"), "kid")
    assert emptied is not None and emptied.member_ids == []
    intact = leave_household(family("mum", "dad"), "kid")
    assert intact is not None and intact.member_ids == ["mum", "dad"]
