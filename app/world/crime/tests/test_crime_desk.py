"""Tests for world/crime/desk.py: the crime complaints desk."""

from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.crime.desk import ComplaintDesk
from world.crime.errors import CaseAlreadyClosed, CaseNotFound, NotAtRequiredLocation
from world.crime.events import EVENT_CASE_OPENED, EVENT_COMPLAINT_FILED
from world.crime.models import CaseStatus, CrimeKind
from world.crime.store import CrimeStore
from world.crime.tests.conftest import EventRecorder


@pytest.mark.asyncio
async def test_first_complaint_opens_a_new_case(recorder: EventRecorder) -> None:
    desk = ComplaintDesk(CrimeStore(), event_sink=recorder.sink)

    complaint, case = await desk.file_complaint(
        "victim", CrimeKind.THEFT, "my wallet is short", LOCATION_GOVERNMENT_COMPLEX, now_tick=1,
        accused_agent_id="suspect",
    )

    assert complaint.case_id == case.case_id
    assert case.status == CaseStatus.OPEN
    assert case.accused_agent_id == "suspect"
    assert recorder.of_type(EVENT_CASE_OPENED)
    assert recorder.of_type(EVENT_COMPLAINT_FILED)


@pytest.mark.asyncio
async def test_second_complaint_attaches_to_existing_case(recorder: EventRecorder) -> None:
    desk = ComplaintDesk(CrimeStore(), event_sink=recorder.sink)
    _, case = await desk.file_complaint(
        "victim", CrimeKind.THEFT, "first account", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    complaint2, case2 = await desk.file_complaint(
        "bystander", CrimeKind.THEFT, "I saw it too", LOCATION_GOVERNMENT_COMPLEX, now_tick=2,
        case_id=case.case_id,
    )

    assert case2.case_id == case.case_id
    complaints = await desk.list_complaints(case.case_id)
    assert len(complaints) == 2
    assert complaint2 in complaints
    assert len(recorder.of_type(EVENT_CASE_OPENED)) == 1
    assert len(recorder.of_type(EVENT_COMPLAINT_FILED)) == 2


@pytest.mark.asyncio
async def test_filing_requires_government_complex(recorder: EventRecorder) -> None:
    desk = ComplaintDesk(CrimeStore(), event_sink=recorder.sink)

    with pytest.raises(NotAtRequiredLocation):
        await desk.file_complaint("victim", CrimeKind.THEFT, "desc", LOCATION_TOWN_HALL, now_tick=0)


@pytest.mark.asyncio
async def test_attaching_to_unknown_case_raises(recorder: EventRecorder) -> None:
    desk = ComplaintDesk(CrimeStore(), event_sink=recorder.sink)

    with pytest.raises(CaseNotFound):
        await desk.file_complaint(
            "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0, case_id="no-such-case"
        )


@pytest.mark.asyncio
async def test_attaching_to_closed_case_raises(recorder: EventRecorder) -> None:
    store = CrimeStore()
    desk = ComplaintDesk(store, event_sink=recorder.sink)
    _, case = await desk.file_complaint(
        "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    case.status = CaseStatus.CLOSED_DISMISSED
    await store.put_case(case)

    with pytest.raises(CaseAlreadyClosed):
        await desk.file_complaint(
            "someone_else", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=1,
            case_id=case.case_id,
        )


@pytest.mark.asyncio
async def test_witness_event_link_is_not_reachable_from_case_or_complaint(recorder: EventRecorder) -> None:
    """The whole point of models.py's tiered design: a complainant citing
    a witness_event_id must never make it visible on the CaseFile or
    ComplaintRecord this method returns - only metrics.py's
    list_ground_truth_links() can see it (see test_crime_metrics.py)."""
    store = CrimeStore()
    desk = ComplaintDesk(store, event_sink=recorder.sink)

    complaint, case = await desk.file_complaint(
        "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0,
        witness_event_id="witness-ground-truth-123",
    )

    assert "witness_event_id" not in type(case).model_fields
    assert "witness_event_id" not in type(complaint).model_fields
    links = await store.list_ground_truth_links()
    assert links[case.case_id] == "witness-ground-truth-123"


@pytest.mark.asyncio
async def test_get_case_raises_for_unknown_id(recorder: EventRecorder) -> None:
    desk = ComplaintDesk(CrimeStore(), event_sink=recorder.sink)
    with pytest.raises(CaseNotFound):
        await desk.get_case("nope")
