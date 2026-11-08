"""Tests for world/charter/government.py: tax rate, credit-store margin,
jobs, police hires, permits, builder licenses, tenders, pension config.
"""

from __future__ import annotations

import pytest

from world.charter.errors import (
    CharterValidationError,
    JobNotFound,
    LicenseAlreadyRevoked,
    LicenseNotFound,
    NotAtRequiredLocation,
    NotSittingGovernment,
    TenderNotFound,
    TenderNotOpen,
)
from world.charter.events import EVENT_POLICE_HIRED, EVENT_TAX_RATE_SET
from world.charter.genesis import seed_genesis
from world.charter.government import GovernmentActions
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_TOWN_HALL,
    GovernmentState,
    JobStatus,
    LicenseStatus,
    TenderStatus,
)
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder

GOVERNMENT = GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1", "m2", "m3", "m4"])


async def make_actions(
    recorder: EventRecorder | None = None,
) -> tuple[GovernmentActions, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    await store.set_government_state(GOVERNMENT)
    return GovernmentActions(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_set_tax_rate_requires_government_complex() -> None:
    actions, _ = await make_actions()

    with pytest.raises(NotAtRequiredLocation):
        await actions.set_tax_rate(500, "pm", LOCATION_TOWN_HALL, now_tick=0)


@pytest.mark.asyncio
async def test_set_tax_rate_requires_sitting_government() -> None:
    actions, _ = await make_actions()

    with pytest.raises(NotSittingGovernment):
        await actions.set_tax_rate(500, "not-a-minister", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)


@pytest.mark.asyncio
async def test_set_tax_rate_by_any_seat_holder_succeeds() -> None:
    recorder = EventRecorder()
    actions, store = await make_actions(recorder)

    policy = await actions.set_tax_rate(500, "m2", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)
    assert policy.tax_rate_bps == 500
    assert policy.version == 1
    assert (await store.get_policy()).tax_rate_bps == 500
    assert recorder.of_type(EVENT_TAX_RATE_SET)


@pytest.mark.asyncio
async def test_set_tax_rate_out_of_range_raises_validation_error() -> None:
    actions, _ = await make_actions()

    with pytest.raises(CharterValidationError):
        await actions.set_tax_rate(20_000, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)


@pytest.mark.asyncio
async def test_set_credit_store_margin() -> None:
    actions, store = await make_actions()
    await actions.set_credit_store_margin(250, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)
    assert (await store.get_policy()).credit_store_margin_bps == 250


@pytest.mark.asyncio
async def test_set_pension_config_updates_only_supplied_fields() -> None:
    actions, store = await make_actions()
    await actions.set_pension_config("pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0, private_pct=30)

    policy = await store.get_policy()
    assert policy.pension_private_pct == 30
    assert policy.pension_government_pct == 50


@pytest.mark.asyncio
async def test_create_job_and_cut_job() -> None:
    actions, _ = await make_actions()
    job = await actions.create_job(
        "baker", "business", 1000, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    assert job.status == JobStatus.OPEN

    cut = await actions.cut_job(job.job_id, "m1", LOCATION_GOVERNMENT_COMPLEX, now_tick=5)
    assert cut.status == JobStatus.CUT
    assert cut.cut_tick == 5


@pytest.mark.asyncio
async def test_cut_unknown_job_raises() -> None:
    actions, _ = await make_actions()
    with pytest.raises(JobNotFound):
        await actions.cut_job("does-not-exist", "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)


@pytest.mark.asyncio
async def test_create_job_rejects_negative_salary() -> None:
    actions, _ = await make_actions()
    with pytest.raises(CharterValidationError):
        await actions.create_job(
            "baker", "business", -1, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )


@pytest.mark.asyncio
async def test_hire_police_creates_a_filled_job() -> None:
    recorder = EventRecorder()
    actions, _ = await make_actions(recorder)
    job = await actions.hire_police(
        "agent-officer", 1500, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )

    assert job.job_type == "police"
    assert job.status == JobStatus.FILLED
    assert job.assigned_agent_id == "agent-officer"
    assert recorder.of_type(EVENT_POLICE_HIRED)


@pytest.mark.asyncio
async def test_issue_permit_and_revoke() -> None:
    actions, _ = await make_actions()
    license_ = await actions.issue_permit(
        "building", "agent-1", 200, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    assert license_.license_type == "permit:building"
    assert license_.status == LicenseStatus.ACTIVE

    revoked = await actions.revoke_license(
        license_.license_id, "m1", "violation", LOCATION_GOVERNMENT_COMPLEX, now_tick=5
    )
    assert revoked.status == LicenseStatus.REVOKED
    assert revoked.revoke_reason == "violation"


@pytest.mark.asyncio
async def test_issue_builder_license() -> None:
    actions, _ = await make_actions()
    license_ = await actions.issue_builder_license(
        "agent-1", 500, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    assert license_.license_type == "builder_license"


@pytest.mark.asyncio
async def test_revoke_already_revoked_license_raises() -> None:
    actions, _ = await make_actions()
    license_ = await actions.issue_builder_license(
        "agent-1", 500, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    await actions.revoke_license(
        license_.license_id, "pm", "r", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )

    with pytest.raises(LicenseAlreadyRevoked):
        await actions.revoke_license(
            license_.license_id, "pm", "r again", LOCATION_GOVERNMENT_COMPLEX, now_tick=2
        )


@pytest.mark.asyncio
async def test_revoke_unknown_license_raises() -> None:
    actions, _ = await make_actions()
    with pytest.raises(LicenseNotFound):
        await actions.revoke_license(
            "does-not-exist", "pm", "r", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )


@pytest.mark.asyncio
async def test_post_award_and_cancel_tender() -> None:
    actions, _ = await make_actions()
    tender = await actions.post_tender(
        "build a mill", "desc", 5000, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    assert tender.status == TenderStatus.OPEN

    awarded = await actions.award_tender(
        tender.tender_id, "construction-co", "m1", LOCATION_GOVERNMENT_COMPLEX, now_tick=5
    )
    assert awarded.status == TenderStatus.AWARDED
    assert awarded.awarded_to_agent_id == "construction-co"


@pytest.mark.asyncio
async def test_award_already_awarded_tender_raises() -> None:
    actions, _ = await make_actions()
    tender = await actions.post_tender(
        "build a mill", "desc", 5000, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    await actions.award_tender(
        tender.tender_id, "construction-co", "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=5
    )

    with pytest.raises(TenderNotOpen):
        await actions.award_tender(
            tender.tender_id, "another-co", "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=6
        )


@pytest.mark.asyncio
async def test_award_unknown_tender_raises() -> None:
    actions, _ = await make_actions()
    with pytest.raises(TenderNotFound):
        await actions.award_tender(
            "does-not-exist", "co", "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
        )


@pytest.mark.asyncio
async def test_cancel_tender() -> None:
    actions, _ = await make_actions()
    tender = await actions.post_tender(
        "build a mill", "desc", 5000, "pm", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    cancelled = await actions.cancel_tender(
        tender.tender_id, "pm", "budget cut", LOCATION_GOVERNMENT_COMPLEX, now_tick=1
    )
    assert cancelled.status == TenderStatus.CANCELLED
    assert cancelled.cancel_reason == "budget cut"
