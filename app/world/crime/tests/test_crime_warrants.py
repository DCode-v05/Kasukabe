"""Tests for world/crime/warrants.py: warrant requests and minister
signatures.
"""

from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.crime.desk import ComplaintDesk
from world.crime.errors import NotAtRequiredLocation, NotMinister, NotPoliceOfficer, WarrantNotPending
from world.crime.events import EVENT_WARRANT_DENIED, EVENT_WARRANT_ISSUED, EVENT_WARRANT_REQUESTED
from world.crime.models import CrimeKind, WarrantStatus
from world.crime.store import CrimeStore
from world.crime.tests.conftest import EventRecorder, FakeGovernment
from world.crime.warrants import WarrantOffice


async def open_case(store: CrimeStore) -> str:
    desk = ComplaintDesk(store)
    _, case = await desk.file_complaint(
        "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    return case.case_id


@pytest.mark.asyncio
async def test_officer_requests_a_warrant(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    office = WarrantOffice(store, government, event_sink=recorder.sink)

    warrant = await office.request_warrant(
        case_id, "officer_a", "suspect", "witness statements point to suspect",
        LOCATION_GOVERNMENT_COMPLEX, now_tick=1,
    )

    assert warrant.status == WarrantStatus.REQUESTED
    assert warrant.target_agent_id == "suspect"
    assert recorder.of_type(EVENT_WARRANT_REQUESTED)


@pytest.mark.asyncio
async def test_only_police_can_request(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids=set())
    office = WarrantOffice(store, government, event_sink=recorder.sink)

    with pytest.raises(NotPoliceOfficer):
        await office.request_warrant(
            case_id, "not_an_officer", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
        )


@pytest.mark.asyncio
async def test_only_minister_can_sign(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids=set())
    office = WarrantOffice(store, government, event_sink=recorder.sink)
    warrant = await office.request_warrant(
        case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    with pytest.raises(NotMinister):
        await office.sign_warrant(warrant.warrant_id, "officer_a", LOCATION_GOVERNMENT_COMPLEX, now_tick=2)


@pytest.mark.asyncio
async def test_minister_signature_grants_the_warrant(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    office = WarrantOffice(store, government, event_sink=recorder.sink)
    warrant = await office.request_warrant(
        case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    signed = await office.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=2)

    assert signed.status == WarrantStatus.GRANTED
    assert signed.decided_by_minister_agent_id == "minister_m"
    assert recorder.of_type(EVENT_WARRANT_ISSUED)


@pytest.mark.asyncio
async def test_minister_can_deny_with_a_reason(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    office = WarrantOffice(store, government, event_sink=recorder.sink)
    warrant = await office.request_warrant(
        case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    denied = await office.deny_warrant(
        warrant.warrant_id, "minister_m", "insufficient evidence", LOCATION_GOVERNMENT_COMPLEX, now_tick=2
    )

    assert denied.status == WarrantStatus.DENIED
    assert denied.denial_reason == "insufficient evidence"
    assert recorder.of_type(EVENT_WARRANT_DENIED)


@pytest.mark.asyncio
async def test_cannot_sign_an_already_decided_warrant(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    office = WarrantOffice(store, government, event_sink=recorder.sink)
    warrant = await office.request_warrant(
        case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    await office.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=2)

    with pytest.raises(WarrantNotPending):
        await office.sign_warrant(warrant.warrant_id, "minister_m", LOCATION_GOVERNMENT_COMPLEX, now_tick=3)


@pytest.mark.asyncio
async def test_warrant_actions_require_government_complex(recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    office = WarrantOffice(store, government, event_sink=recorder.sink)

    with pytest.raises(NotAtRequiredLocation):
        await office.request_warrant(case_id, "officer_a", "suspect", "j", LOCATION_TOWN_HALL, now_tick=1)
