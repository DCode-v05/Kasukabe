"""Tests for world/crime/investigation.py: the "never omniscient"
interview and evidence-visibility model.
"""

from __future__ import annotations

import pytest

from world.crime.desk import ComplaintDesk
from world.crime.errors import CaseAlreadyClosed, NotCoLocated, NotPoliceOfficer
from world.crime.investigation import InvestigationBoard
from world.crime.models import CaseStatus, CrimeKind, InterviewVisibility
from world.crime.store import CrimeStore
from world.crime.tests.conftest import EventRecorder, FakeGovernment, FakeLocation

GOVERNMENT_COMPLEX = "government_complex"


async def open_case(store: CrimeStore) -> str:
    desk = ComplaintDesk(store)
    _, case = await desk.file_complaint("victim", CrimeKind.THEFT, "desc", GOVERNMENT_COMPLEX, now_tick=0)
    return case.case_id


@pytest.mark.asyncio
async def test_officer_sees_public_records_and_own_private_interviews(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a", "officer_b"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)

    location.place("officer_a", "park")
    location.place("witness1", "park")
    await board.log_interview(case_id, "officer_a", "witness1", "saw nothing", now_tick=1)

    location.place("officer_a", "cafe")
    location.place("suspect", "cafe")
    await board.log_interview(
        case_id, "officer_a", "suspect", "denies everything", now_tick=2, visibility=InterviewVisibility.PRIVATE
    )

    view = await board.read_case_evidence(case_id, "officer_a")
    assert len(view.complaints) == 1
    assert len(view.visible_interviews) == 2


@pytest.mark.asyncio
async def test_officer_cannot_see_a_colleagues_private_interview(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a", "officer_b"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)

    location.place("officer_a", "cafe")
    location.place("suspect", "cafe")
    await board.log_interview(
        case_id, "officer_a", "suspect", "officer_a's private notes", now_tick=1,
        visibility=InterviewVisibility.PRIVATE,
    )

    view = await board.read_case_evidence(case_id, "officer_b")
    assert view.visible_interviews == []


@pytest.mark.asyncio
async def test_officer_sees_a_public_interview_logged_by_a_colleague(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a", "officer_b"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)

    location.place("officer_a", "cafe")
    location.place("witness1", "cafe")
    await board.log_interview(case_id, "officer_a", "witness1", "on the record", now_tick=1)

    view = await board.read_case_evidence(case_id, "officer_b")
    assert len(view.visible_interviews) == 1


@pytest.mark.asyncio
async def test_case_evidence_view_never_carries_ground_truth(location: FakeLocation, recorder: EventRecorder) -> None:
    """Structural check on models.py's separation: CaseEvidenceView's
    `case` field is a CaseFile, and CaseFile has no field that could
    carry WitnessEvent's perpetrator_agent_id."""
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)

    view = await board.read_case_evidence(case_id, "officer_a")
    assert "witness_event_id" not in type(view.case).model_fields
    assert not hasattr(view.case, "perpetrator_agent_id")


@pytest.mark.asyncio
async def test_non_police_agent_cannot_interview(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids=set())
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)
    location.place("civilian", "park")
    location.place("witness1", "park")

    with pytest.raises(NotPoliceOfficer):
        await board.log_interview(case_id, "civilian", "witness1", "summary", now_tick=1)


@pytest.mark.asyncio
async def test_interview_requires_co_location(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("witness1", "cafe")

    with pytest.raises(NotCoLocated):
        await board.log_interview(case_id, "officer_a", "witness1", "summary", now_tick=1)


@pytest.mark.asyncio
async def test_first_interview_moves_case_to_under_investigation(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    government = FakeGovernment(police_ids={"officer_a"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("witness1", "park")

    await board.log_interview(case_id, "officer_a", "witness1", "summary", now_tick=1)

    case = await store.get_case(case_id)
    assert case is not None
    assert case.status == CaseStatus.UNDER_INVESTIGATION


@pytest.mark.asyncio
async def test_cannot_interview_on_a_closed_case(location: FakeLocation, recorder: EventRecorder) -> None:
    store = CrimeStore()
    case_id = await open_case(store)
    case = await store.get_case(case_id)
    assert case is not None
    case.status = CaseStatus.CLOSED_DISMISSED
    await store.put_case(case)

    government = FakeGovernment(police_ids={"officer_a"})
    board = InvestigationBoard(store, government, location, event_sink=recorder.sink)
    location.place("officer_a", "park")
    location.place("witness1", "park")

    with pytest.raises(CaseAlreadyClosed):
        await board.log_interview(case_id, "officer_a", "witness1", "summary", now_tick=1)
