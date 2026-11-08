"""Tests for world/crime/fights.py: the fight mechanic. DESIGN.md section
6: "Fights injure (feeds health system) - killing is excluded by world
physics" - every test here asserts an injury or a witness event, never a
death, because there is nothing in fights.py that could produce one.
"""

from __future__ import annotations

import random

import pytest

from world.crime.errors import CannotTargetSelf, NotCoLocated
from world.crime.events import EVENT_FIGHT_OCCURRED
from world.crime.fights import FightRing
from world.crime.models import CrimeKind, InjurySeverity
from world.crime.store import CrimeStore
from world.crime.tests.conftest import EventRecorder, FakeHealth, FakeLocation


def make_ring(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> FightRing:
    return FightRing(store=CrimeStore(), health=health, location=location, event_sink=recorder.sink)


class AlwaysLow(random.Random):
    def random(self) -> float:
        return 0.0


class AlwaysHigh(random.Random):
    def random(self) -> float:
        return 0.999


@pytest.mark.asyncio
async def test_one_sided_fight_only_injures_defendant(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("bully", "park")
    location.place("victim", "park")
    ring = make_ring(health, location, recorder)

    result = await ring.resolve_fight("bully", "victim", tick=1, rng=AlwaysLow(), mutual=False)

    assert len(result.injuries) == 1
    assert result.injuries[0].agent_id == "victim"
    assert health.injuries == [("victim", result.injuries[0].severity, 1)]
    assert recorder.of_type(EVENT_FIGHT_OCCURRED)


@pytest.mark.asyncio
async def test_mutual_fight_can_injure_both(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("a", "park")
    location.place("b", "park")
    ring = make_ring(health, location, recorder)

    result = await ring.resolve_fight("a", "b", tick=1, rng=AlwaysLow(), mutual=True)

    injured = {injury.agent_id for injury in result.injuries}
    assert injured == {"a", "b"}
    assert {agent_id for agent_id, _, _ in health.injuries} == {"a", "b"}


@pytest.mark.asyncio
async def test_fight_can_end_with_no_injuries(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("a", "park")
    location.place("b", "park")
    ring = make_ring(health, location, recorder)

    result = await ring.resolve_fight("a", "b", tick=1, rng=AlwaysHigh(), mutual=True)

    assert result.injuries == []
    assert health.injuries == []


@pytest.mark.asyncio
async def test_fight_ground_truth_always_names_real_aggressor(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("a", "park")
    location.place("b", "park")
    store = CrimeStore()
    ring = FightRing(store=store, health=health, location=location, event_sink=recorder.sink)

    result = await ring.resolve_fight("a", "b", tick=7, rng=AlwaysHigh())

    event = await store.get_witness_event(result.witness_event_id)
    assert event is not None
    assert event.kind is CrimeKind.ASSAULT
    assert event.perpetrator_agent_id == "a"
    assert event.victim_agent_id == "b"


@pytest.mark.asyncio
async def test_fight_requires_co_location(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("a", "park")
    location.place("b", "cafe")
    ring = make_ring(health, location, recorder)

    with pytest.raises(NotCoLocated):
        await ring.resolve_fight("a", "b", tick=0, rng=random.Random(1))


@pytest.mark.asyncio
async def test_fight_rejects_self_target(health: FakeHealth, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("a", "park")
    ring = make_ring(health, location, recorder)

    with pytest.raises(CannotTargetSelf):
        await ring.resolve_fight("a", "a", tick=0, rng=random.Random(1))


def test_injury_severity_is_never_a_death() -> None:
    """Structural assertion: InjurySeverity has exactly the three
    non-lethal grades models.py defines, nothing worse."""
    assert set(InjurySeverity) == {InjurySeverity.MINOR, InjurySeverity.MODERATE, InjurySeverity.SEVERE}
