"""Tests for world/charter/land_petitions.py: government petitions to
the President, and the President's approve/deny.
"""

from __future__ import annotations

import pytest

from world.charter.errors import (
    NotAtRequiredLocation,
    NotSittingGovernment,
    PetitionNotFound,
    PetitionNotPending,
)
from world.charter.events import (
    EVENT_PETITION_APPROVED,
    EVENT_PETITION_DENIED,
    EVENT_PETITION_FILED,
)
from world.charter.genesis import seed_genesis
from world.charter.land_petitions import LandPetitionQueue
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_TOWN_HALL,
    GovernmentState,
    PetitionStatus,
)
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder

GOVERNMENT = GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1"])


async def make_queue(
    recorder: EventRecorder | None = None,
) -> tuple[LandPetitionQueue, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    await store.set_government_state(GOVERNMENT)
    return LandPetitionQueue(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_file_petition_requires_government_complex() -> None:
    queue, _ = await make_queue()

    with pytest.raises(NotAtRequiredLocation):
        await queue.file_petition(
            "pm", "expand north", "more farmland", 20, LOCATION_TOWN_HALL, now_tick=0
        )


@pytest.mark.asyncio
async def test_file_petition_requires_sitting_government() -> None:
    queue, _ = await make_queue()

    with pytest.raises(NotSittingGovernment):
        await queue.file_petition(
            "outsider", "expand north", "more farmland", 20, LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )


@pytest.mark.asyncio
async def test_file_petition_succeeds_and_flows_to_pending_queue() -> None:
    recorder = EventRecorder()
    queue, _ = await make_queue(recorder)

    petition = await queue.file_petition(
        "pm", "expand north", "more farmland", 20, LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    assert petition.status == PetitionStatus.PENDING
    assert recorder.of_type(EVENT_PETITION_FILED)

    pending = await queue.list_pending()
    assert [p.petition_id for p in pending] == [petition.petition_id]


@pytest.mark.asyncio
async def test_approve_petition_moves_it_out_of_pending() -> None:
    recorder = EventRecorder()
    queue, _ = await make_queue(recorder)
    petition = await queue.file_petition(
        "pm", "expand north", "more farmland", 20, LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )

    approved = await queue.approve_petition(
        petition.petition_id, "approved for research value", now_tick=10
    )
    assert approved.status == PetitionStatus.APPROVED
    assert approved.decision_note == "approved for research value"
    assert await queue.list_pending() == []
    assert recorder.of_type(EVENT_PETITION_APPROVED)


@pytest.mark.asyncio
async def test_deny_petition() -> None:
    recorder = EventRecorder()
    queue, _ = await make_queue(recorder)
    petition = await queue.file_petition(
        "pm", "expand north", "more farmland", 20, LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )

    denied = await queue.deny_petition(petition.petition_id, "not now", now_tick=10)
    assert denied.status == PetitionStatus.DENIED
    assert recorder.of_type(EVENT_PETITION_DENIED)


@pytest.mark.asyncio
async def test_deciding_unknown_petition_raises() -> None:
    queue, _ = await make_queue()
    with pytest.raises(PetitionNotFound):
        await queue.approve_petition("does-not-exist", "note", now_tick=0)


@pytest.mark.asyncio
async def test_deciding_already_decided_petition_raises() -> None:
    queue, _ = await make_queue()
    petition = await queue.file_petition(
        "pm", "expand north", "more farmland", 20, LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    await queue.approve_petition(petition.petition_id, "ok", now_tick=1)

    with pytest.raises(PetitionNotPending):
        await queue.deny_petition(petition.petition_id, "too late", now_tick=2)
