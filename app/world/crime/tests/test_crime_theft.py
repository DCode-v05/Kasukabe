"""Tests for world/crime/theft.py: the `steal` primitive's resolution."""

from __future__ import annotations

import random

import pytest

from world.crime.errors import CannotTargetSelf, InvalidCrimeAmount, NotCoLocated
from world.crime.events import EVENT_THEFT_ATTEMPTED
from world.crime.store import CrimeStore
from world.crime.theft import TheftDesk
from world.crime.tests.conftest import EventRecorder, FakeEconomy, FakeLocation


def make_desk(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> TheftDesk:
    return TheftDesk(store=CrimeStore(), economy=economy, location=location, event_sink=recorder.sink)


@pytest.mark.asyncio
async def test_successful_theft_moves_sim_and_records_ground_truth(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> None:
    economy.sim_balances["victim"] = 500
    location.place("thief", "park")
    location.place("victim", "park")
    desk = make_desk(economy, location, recorder)
    class AlwaysSucceed(random.Random):
        def random(self) -> float:
            return 0.0

    event = await desk.attempt_theft("thief", "victim", 100, tick=5, rng=AlwaysSucceed())

    assert event.theft_succeeded is True
    assert event.perpetrator_agent_id == "thief"
    assert event.victim_agent_id == "victim"
    assert event.amount_sim == 100
    assert economy.sim_balances["victim"] == 400
    assert economy.sim_balances["thief"] == 100
    assert recorder.of_type(EVENT_THEFT_ATTEMPTED)


@pytest.mark.asyncio
async def test_failed_theft_moves_no_money(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> None:
    economy.sim_balances["victim"] = 500
    location.place("thief", "park")
    location.place("victim", "park")
    desk = make_desk(economy, location, recorder)

    class AlwaysFail(random.Random):
        def random(self) -> float:
            return 0.99

    event = await desk.attempt_theft("thief", "victim", 100, tick=5, rng=AlwaysFail())

    assert event.theft_succeeded is False
    assert economy.sim_balances["victim"] == 500
    assert "thief" not in economy.sim_balances


@pytest.mark.asyncio
async def test_witness_only_generated_from_supplied_candidates(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("thief", "park")
    location.place("victim", "park")
    desk = make_desk(economy, location, recorder)

    class FailButWitness(random.Random):
        """First draw (success roll) is high -> fails; second draw
        (witness roll) is low -> witnessed. Keeps the two rolls
        independent per DESIGN.md's own wording, and avoids needing to
        fund the victim's wallet since nothing actually moves."""

        def __init__(self) -> None:
            super().__init__()
            self.draws = iter([0.99, 0.0])

        def random(self) -> float:
            return next(self.draws)

    no_candidates = await desk.attempt_theft(
        "thief", "victim", 50, tick=1, rng=FailButWitness(), potential_witness_agent_ids=[]
    )
    assert no_candidates.witnessed is False
    assert no_candidates.witness_agent_ids == []

    with_candidates = await desk.attempt_theft(
        "thief", "victim", 50, tick=2, rng=FailButWitness(), potential_witness_agent_ids=["bystander"]
    )
    assert with_candidates.witnessed is True
    assert with_candidates.witness_agent_ids == ["bystander"]


@pytest.mark.asyncio
async def test_theft_requires_co_location(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("thief", "park")
    location.place("victim", "cafe")
    desk = make_desk(economy, location, recorder)

    with pytest.raises(NotCoLocated):
        await desk.attempt_theft("thief", "victim", 100, tick=0, rng=random.Random(1))


@pytest.mark.asyncio
async def test_theft_rejects_self_target(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("thief", "park")
    desk = make_desk(economy, location, recorder)

    with pytest.raises(CannotTargetSelf):
        await desk.attempt_theft("thief", "thief", 100, tick=0, rng=random.Random(1))


@pytest.mark.asyncio
async def test_theft_rejects_non_positive_amount(economy: FakeEconomy, location: FakeLocation, recorder: EventRecorder) -> None:
    location.place("thief", "park")
    location.place("victim", "park")
    desk = make_desk(economy, location, recorder)

    with pytest.raises(InvalidCrimeAmount):
        await desk.attempt_theft("thief", "victim", 0, tick=0, rng=random.Random(1))
