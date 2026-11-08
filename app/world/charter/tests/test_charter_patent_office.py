"""Tests for world/charter/patent_office.py: file, approve, reject,
revoke.
"""

from __future__ import annotations

import pytest

from world.charter.errors import (
    InsufficientFee,
    NotAtRequiredLocation,
    NotSittingGovernment,
    PatentAlreadyRevoked,
    PatentApplicationNotFound,
    PatentApplicationNotPending,
    PatentNotFound,
)
from world.charter.events import EVENT_PATENT_APPROVED, EVENT_PATENT_FILED, EVENT_PATENT_REVOKED
from world.charter.genesis import seed_genesis
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_PATENT_OFFICE,
    GovernmentState,
    PatentStatus,
)
from world.charter.patent_office import PatentOffice
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder

GOVERNMENT = GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1"])


async def make_office(recorder: EventRecorder | None = None) -> tuple[PatentOffice, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    await store.set_government_state(GOVERNMENT)
    return PatentOffice(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_file_application_requires_patent_office_location() -> None:
    office, _ = await make_office()

    with pytest.raises(NotAtRequiredLocation):
        await office.file_application(
            "inventor-1",
            "auto-harvest",
            "harvests a whole field",
            ["harvest"],
            1000,
            LOCATION_GOVERNMENT_COMPLEX,
            now_tick=0,
        )


@pytest.mark.asyncio
async def test_file_application_below_fee_rejected() -> None:
    office, _ = await make_office()

    with pytest.raises(InsufficientFee):
        await office.file_application(
            "inventor-1",
            "auto-harvest",
            "harvests a whole field",
            ["harvest"],
            500,
            LOCATION_PATENT_OFFICE,
            now_tick=0,
        )


@pytest.mark.asyncio
async def test_file_application_succeeds_and_is_pending() -> None:
    recorder = EventRecorder()
    office, _ = await make_office(recorder)

    application = await office.file_application(
        "inventor-1",
        "auto-harvest",
        "harvests a whole field",
        ["harvest"],
        1000,
        LOCATION_PATENT_OFFICE,
        now_tick=0,
    )
    assert application.status == PatentStatus.PENDING
    assert recorder.of_type(EVENT_PATENT_FILED)


@pytest.mark.asyncio
async def test_approve_application_requires_sitting_government_reviewer() -> None:
    office, _ = await make_office()
    application = await office.file_application(
        "inventor-1", "auto-harvest", "d", [], 1000, LOCATION_PATENT_OFFICE, now_tick=0
    )

    with pytest.raises(NotSittingGovernment):
        await office.approve_application(
            application.application_id, "outsider", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
        )


@pytest.mark.asyncio
async def test_approve_application_creates_active_patent_owned_by_inventor() -> None:
    recorder = EventRecorder()
    office, store = await make_office(recorder)
    application = await office.file_application(
        "inventor-1", "auto-harvest", "d", [], 1000, LOCATION_PATENT_OFFICE, now_tick=0
    )

    patent = await office.approve_application(
        application.application_id, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    assert patent.owner_agent_id == "inventor-1"
    assert patent.revoked is False
    assert recorder.of_type(EVENT_PATENT_APPROVED)

    reloaded_application = await store.get_patent_application(application.application_id)
    assert reloaded_application is not None
    assert reloaded_application.status == PatentStatus.APPROVED


@pytest.mark.asyncio
async def test_approve_unknown_application_raises() -> None:
    office, _ = await make_office()
    with pytest.raises(PatentApplicationNotFound):
        await office.approve_application(
            "does-not-exist", "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )


@pytest.mark.asyncio
async def test_approve_already_decided_application_raises() -> None:
    office, _ = await make_office()
    application = await office.file_application(
        "inventor-1", "auto-harvest", "d", [], 1000, LOCATION_PATENT_OFFICE, now_tick=0
    )
    await office.approve_application(
        application.application_id, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    with pytest.raises(PatentApplicationNotPending):
        await office.approve_application(
            application.application_id, "m1", LOCATION_GOVERNMENT_COMPLEX, now_tick=2
        )


@pytest.mark.asyncio
async def test_reject_application() -> None:
    office, _ = await make_office()
    application = await office.file_application(
        "inventor-1", "auto-harvest", "d", [], 1000, LOCATION_PATENT_OFFICE, now_tick=0
    )
    rejected = await office.reject_application(
        application.application_id, "pm", "too broad", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    assert rejected.status == PatentStatus.REJECTED
    assert rejected.reject_reason == "too broad"


@pytest.mark.asyncio
async def test_revoke_patent() -> None:
    recorder = EventRecorder()
    office, _ = await make_office(recorder)
    application = await office.file_application(
        "inventor-1", "auto-harvest", "d", [], 1000, LOCATION_PATENT_OFFICE, now_tick=0
    )
    patent = await office.approve_application(
        application.application_id, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    revoked = await office.revoke_patent(
        patent.patent_id, "m1", "misused", LOCATION_GOVERNMENT_COMPLEX, now_tick=2
    )
    assert revoked.revoked is True
    assert revoked.revoke_reason == "misused"
    assert recorder.of_type(EVENT_PATENT_REVOKED)


@pytest.mark.asyncio
async def test_revoke_unknown_patent_raises() -> None:
    office, _ = await make_office()
    with pytest.raises(PatentNotFound):
        await office.revoke_patent(
            "does-not-exist", "pm", "r", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )


@pytest.mark.asyncio
async def test_revoke_already_revoked_patent_raises() -> None:
    office, _ = await make_office()
    application = await office.file_application(
        "inventor-1", "auto-harvest", "d", [], 1000, LOCATION_PATENT_OFFICE, now_tick=0
    )
    patent = await office.approve_application(
        application.application_id, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    await office.revoke_patent(patent.patent_id, "pm", "r", LOCATION_GOVERNMENT_COMPLEX, now_tick=2)

    with pytest.raises(PatentAlreadyRevoked):
        await office.revoke_patent(
            patent.patent_id, "pm", "r again", LOCATION_GOVERNMENT_COMPLEX, now_tick=3
        )
