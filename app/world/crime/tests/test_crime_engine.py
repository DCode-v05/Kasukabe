"""Tests for world/crime/engine.py: the CrimeEngine facade, exercised as
one end-to-end pipeline (steal -> complain -> investigate -> warrant ->
arrest -> fine) so the wiring between every sub-desk is proven, not just
each desk in isolation.
"""

from __future__ import annotations

import random

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.crime.engine import create_crime_engine
from world.crime.models import CaseStatus, CrimeKind, OutcomeKind
from world.crime.tests.conftest import EventRecorder, FakeEconomy, FakeGovernment, FakeHealth, FakeLocation


@pytest.mark.asyncio
async def test_full_theft_to_fine_pipeline() -> None:
    economy = FakeEconomy(sim_balances={"victim": 500})
    health = FakeHealth()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    location = FakeLocation()
    recorder = EventRecorder()
    engine = create_crime_engine(economy, health, government, location, event_sink=recorder.sink)

    location.place("thief", "park")
    location.place("victim", "park")

    class AlwaysSucceed(random.Random):
        def random(self) -> float:
            return 0.0

    witness_event = await engine.theft.attempt_theft(
        "thief", "victim", 100, tick=0, rng=AlwaysSucceed(), potential_witness_agent_ids=["bystander"]
    )
    assert witness_event.theft_succeeded is True
    assert economy.sim_balances["thief"] == 100

    complaint, case = await engine.desk.file_complaint(
        "victim", CrimeKind.THEFT, "my sim went missing", LOCATION_GOVERNMENT_COMPLEX, now_tick=1,
        accused_agent_id="thief", witness_event_id=witness_event.witness_event_id,
    )

    location.place("officer_a", "park")
    location.place("bystander", "park")
    await engine.investigation.log_interview(case.case_id, "officer_a", "bystander", "confirms it was thief", now_tick=2)

    warrant = await engine.warrants.request_warrant(
        case.case_id, "officer_a", "thief", "bystander corroborates victim's account",
        LOCATION_GOVERNMENT_COMPLEX, now_tick=3,
    )
    await engine.warrants.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=4)

    await engine.arrests.arrest(case.case_id, "officer_a", "thief", now_tick=5, warrant_id=warrant.warrant_id)

    outcome = await engine.outcomes.impose_fine(
        case.case_id, warrant.warrant_id, "officer_a", 100, LOCATION_GOVERNMENT_COMPLEX, now_tick=6
    )

    assert outcome.kind == OutcomeKind.FINE
    assert outcome.convicted_agent_id == "thief"
    assert economy.sim_balances["thief"] == 0
    assert economy.treasury_sim == 100

    stats = await engine.compute_metrics()
    assert stats.total_convictions == 1
    assert stats.true_convictions == 1
    assert stats.false_convictions == 0
    assert stats.false_conviction_rate == 0.0


@pytest.mark.asyncio
async def test_false_conviction_when_wrong_suspect_is_charged() -> None:
    """The convicted agent need not be the ground-truth thief - the
    police work from claims, not from WitnessEvent - and the metric
    catches exactly that gap."""
    economy = FakeEconomy(sim_balances={"victim": 500, "innocent": 500})
    health = FakeHealth()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    location = FakeLocation()
    engine = create_crime_engine(economy, health, government, location)

    location.place("real_thief", "park")
    location.place("victim", "park")

    class AlwaysSucceed(random.Random):
        def random(self) -> float:
            return 0.0

    witness_event = await engine.theft.attempt_theft(
        "real_thief", "victim", 100, tick=0, rng=AlwaysSucceed()
    )

    _, case = await engine.desk.file_complaint(
        "victim", CrimeKind.THEFT, "wrongly blamed innocent", LOCATION_GOVERNMENT_COMPLEX, now_tick=1,
        accused_agent_id="innocent", witness_event_id=witness_event.witness_event_id,
    )
    warrant = await engine.warrants.request_warrant(
        case.case_id, "officer_a", "innocent", "mistaken identity", LOCATION_GOVERNMENT_COMPLEX, now_tick=2
    )
    await engine.warrants.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=3)
    await engine.outcomes.impose_fine(
        case.case_id, warrant.warrant_id, "officer_a", 50, LOCATION_GOVERNMENT_COMPLEX, now_tick=4
    )

    stats = await engine.compute_metrics()
    assert stats.total_convictions == 1
    assert stats.false_convictions == 1
    assert stats.false_conviction_rate == 1.0


@pytest.mark.asyncio
async def test_fight_pipeline_feeds_health_never_death() -> None:
    economy = FakeEconomy()
    health = FakeHealth()
    government = FakeGovernment()
    location = FakeLocation()
    engine = create_crime_engine(economy, health, government, location)
    location.place("a", "park")
    location.place("b", "park")

    class AlwaysLow(random.Random):
        def random(self) -> float:
            return 0.0

    result = await engine.fights.resolve_fight("a", "b", tick=0, rng=AlwaysLow())

    assert len(result.injuries) >= 1
    assert len(health.injuries) >= 1
    assert not hasattr(result, "died")
    assert not hasattr(result, "is_fatal")


@pytest.mark.asyncio
async def test_jail_locks_confinement_and_then_releases_on_schedule() -> None:
    economy = FakeEconomy()
    health = FakeHealth()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    location = FakeLocation()
    engine = create_crime_engine(economy, health, government, location)

    _, case = await engine.desk.file_complaint(
        "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    warrant = await engine.warrants.request_warrant(
        case.case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    await engine.warrants.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=2)

    _, confinement = await engine.outcomes.impose_jail(
        case.case_id, warrant.warrant_id, "officer_a", jail_hours=6.0, jail_location_id="jail_cell",
        location=LOCATION_GOVERNMENT_COMPLEX, now_tick=10,
    )
    assert await engine.outcomes.tools_locked_to("suspect") == "jail_cell"

    released = await engine.outcomes.release_due_confinements(now_tick=confinement.release_tick)
    assert released
    assert await engine.outcomes.tools_locked_to("suspect") is None

    case_after = await engine.desk.get_case(case.case_id)
    assert case_after.status == CaseStatus.CLOSED_JAIL
