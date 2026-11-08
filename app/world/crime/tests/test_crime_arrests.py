"""Tests for world/crime/arrests.py: the `arrest` primitive."""

from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.crime.arrests import ArrestDesk
from world.crime.desk import ComplaintDesk
from world.crime.errors import NotCoLocated, NotPoliceOfficer, WarrantNotGranted
from world.crime.events import EVENT_ARREST_MADE
from world.crime.models import CrimeKind, WarrantStatus
from world.crime.store import CrimeStore
from world.crime.tests.conftest import EventRecorder, FakeGovernment, FakeLocation
from world.crime.warrants import WarrantOffice


async def open_case(store: CrimeStore) -> str:
    desk = ComplaintDesk(store)
    _, case = await desk.file_complaint(
        "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    return case.case_id


@pytest.mark.asyncio
async def test_arrest_requires_police_officer(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids=set())
    desk = ArrestDesk(store, government, location, event_sink=recorder.sink)
    location.place("civilian", "park")
    location.place("suspect", "park")

    with pytest.raises(NotPoliceOfficer):
        await desk.arrest(case_id, "civilian", "suspect", now_tick=1)


@pytest.mark.asyncio
async def test_arrest_requires_co_location(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    desk = ArrestDesk(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("suspect", "cafe")

    with pytest.raises(NotCoLocated):
        await desk.arrest(case_id, "officer_a", "suspect", now_tick=1)


@pytest.mark.asyncio
async def test_arrest_without_a_warrant_succeeds(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    desk = ArrestDesk(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("suspect", "park")

    record = await desk.arrest(case_id, "officer_a", "suspect", now_tick=1)

    assert record.warrant_id is None
    assert record.location_id == "park"
    assert recorder.of_type(EVENT_ARREST_MADE)


@pytest.mark.asyncio
async def test_arrest_citing_a_granted_warrant_succeeds(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    warrants = WarrantOffice(store, government)
    warrant = await warrants.request_warrant(
        case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    await warrants.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=2)

    desk = ArrestDesk(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("suspect", "park")

    record = await desk.arrest(case_id, "officer_a", "suspect", now_tick=3, warrant_id=warrant.warrant_id)
    assert record.warrant_id == warrant.warrant_id


@pytest.mark.asyncio
async def test_arrest_citing_an_ungranted_warrant_raises(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    warrants = WarrantOffice(store, government)
    warrant = await warrants.request_warrant(
        case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    assert warrant.status == WarrantStatus.REQUESTED

    desk = ArrestDesk(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("suspect", "park")

    with pytest.raises(WarrantNotGranted):
        await desk.arrest(case_id, "officer_a", "suspect", now_tick=3, warrant_id=warrant.warrant_id)
