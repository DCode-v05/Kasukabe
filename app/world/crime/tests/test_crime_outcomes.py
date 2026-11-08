"""Tests for world/crime/outcomes.py: fine, jail, confinement, and
release. DESIGN.md section 6: "fine (ledger) or jail (confinement N
game-hours, no income, tools locked to jail location)".
"""

from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.crime.desk import ComplaintDesk
from world.crime.errors import AlreadyConfined, InvalidCrimeAmount, NotPoliceOfficer, WarrantNotGranted
from world.crime.events import EVENT_CASE_DISMISSED, EVENT_CONFINEMENT_RELEASED, EVENT_FINE_IMPOSED, EVENT_JAIL_IMPOSED
from world.crime.models import CaseStatus, CrimeKind, OutcomeKind, TICKS_PER_GAME_HOUR, WarrantStatus
from world.crime.outcomes import OutcomeDesk
from world.crime.store import CrimeStore
from world.crime.tests.conftest import EventRecorder, FakeEconomy, FakeGovernment
from world.crime.warrants import WarrantOffice


async def open_case_with_granted_warrant(
    store: CrimeStore, government: FakeGovernment, officer: str = "officer_a", minister: str = "minister_m", target: str = "suspect"
) -> tuple[str, str]:
    desk = ComplaintDesk(store)
    _, case = await desk.file_complaint("victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)
    warrants = WarrantOffice(store, government)
    warrant = await warrants.request_warrant(case.case_id, officer, target, "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1)
    await warrants.sign_warrant(warrant.warrant_id, minister, LOCATION_GOVERNMENT_COMPLEX, now_tick=2)
    return case.case_id, warrant.warrant_id


@pytest.mark.asyncio
async def test_impose_fine_moves_money_and_closes_case(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    economy.sim_balances["suspect"] = 1000
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)

    outcome = await desk.impose_fine(
        case_id, warrant_id, "officer_a", 300, LOCATION_GOVERNMENT_COMPLEX, now_tick=3
    )

    assert outcome.kind == OutcomeKind.FINE
    assert outcome.convicted_agent_id == "suspect"
    assert economy.sim_balances["suspect"] == 700
    assert economy.treasury_sim == 300
    case = await store.get_case(case_id)
    assert case is not None
    assert case.status == CaseStatus.CLOSED_FINE
    assert recorder.of_type(EVENT_FINE_IMPOSED)


@pytest.mark.asyncio
async def test_fine_requires_a_granted_warrant(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"})
    desk_ = ComplaintDesk(store)
    _, case = await desk_.file_complaint("victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0)
    warrants = WarrantOffice(store, government)
    warrant = await warrants.request_warrant(case.case_id, "officer_a", "suspect", "j", LOCATION_GOVERNMENT_COMPLEX, now_tick=1)
    assert warrant.status == WarrantStatus.REQUESTED

    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)
    with pytest.raises(WarrantNotGranted):
        await desk.impose_fine(case.case_id, warrant.warrant_id, "officer_a", 100, LOCATION_GOVERNMENT_COMPLEX, now_tick=2)


@pytest.mark.asyncio
async def test_only_police_can_impose_outcomes(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    economy.sim_balances["suspect"] = 1000
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)

    with pytest.raises(NotPoliceOfficer):
        await desk.impose_fine(case_id, warrant_id, "minister_m", 100, LOCATION_GOVERNMENT_COMPLEX, now_tick=3)


@pytest.mark.asyncio
async def test_impose_fine_rejects_non_positive_amount(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)

    with pytest.raises(InvalidCrimeAmount):
        await desk.impose_fine(case_id, warrant_id, "officer_a", 0, LOCATION_GOVERNMENT_COMPLEX, now_tick=3)


@pytest.mark.asyncio
async def test_impose_jail_creates_confinement_with_correct_release_tick(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)

    outcome, confinement = await desk.impose_jail(
        case_id, warrant_id, "officer_a", jail_hours=4.0, jail_location_id="jail_cell_1",
        location=LOCATION_GOVERNMENT_COMPLEX, now_tick=100,
    )

    assert outcome.kind == OutcomeKind.JAIL
    assert confinement.agent_id == "suspect"
    assert confinement.release_tick == 100 + round(4.0 * TICKS_PER_GAME_HOUR)
    assert confinement.released is False
    case = await store.get_case(case_id)
    assert case is not None
    assert case.status == CaseStatus.CLOSED_JAIL
    assert recorder.of_type(EVENT_JAIL_IMPOSED)


@pytest.mark.asyncio
async def test_cannot_jail_an_already_confined_agent(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)
    await desk.impose_jail(
        case_id, warrant_id, "officer_a", jail_hours=4.0, jail_location_id="jail_cell_1",
        location=LOCATION_GOVERNMENT_COMPLEX, now_tick=100,
    )

    case_id2, warrant_id2 = await open_case_with_granted_warrant(store, government, target="suspect")
    with pytest.raises(AlreadyConfined):
        await desk.impose_jail(
            case_id2, warrant_id2, "officer_a", jail_hours=1.0, jail_location_id="jail_cell_1",
            location=LOCATION_GOVERNMENT_COMPLEX, now_tick=101,
        )


@pytest.mark.asyncio
async def test_tools_locked_to_reflects_active_confinement(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)

    assert await desk.tools_locked_to("suspect") is None
    await desk.impose_jail(
        case_id, warrant_id, "officer_a", jail_hours=2.0, jail_location_id="jail_cell_1",
        location=LOCATION_GOVERNMENT_COMPLEX, now_tick=0,
    )
    assert await desk.tools_locked_to("suspect") == "jail_cell_1"


@pytest.mark.asyncio
async def test_release_due_confinements_releases_and_frees_the_agent(economy: FakeEconomy, recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"}, minister_ids={"minister_m"})
    case_id, warrant_id = await open_case_with_granted_warrant(store, government)
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)
    _, confinement = await desk.impose_jail(
        case_id, warrant_id, "officer_a", jail_hours=1.0, jail_location_id="jail_cell_1",
        location=LOCATION_GOVERNMENT_COMPLEX, now_tick=0,
    )

    before_release = await desk.release_due_confinements(now_tick=confinement.release_tick - 1)
    assert before_release == []
    assert await desk.tools_locked_to("suspect") == "jail_cell_1"

    released = await desk.release_due_confinements(now_tick=confinement.release_tick)
    assert [r.confinement_id for r in released] == [confinement.confinement_id]
    assert await desk.tools_locked_to("suspect") is None
    assert recorder.of_type(EVENT_CONFINEMENT_RELEASED)


@pytest.mark.asyncio
async def test_dismiss_case_needs_no_warrant(recorder: EventRecorder) -> None:
    store = CrimeStore()
    government = FakeGovernment(police_ids={"officer_a"})
    complaint_desk = ComplaintDesk(store)
    _, case = await complaint_desk.file_complaint(
        "victim", CrimeKind.THEFT, "desc", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    economy = FakeEconomy()
    desk = OutcomeDesk(store, economy, government, event_sink=recorder.sink)

    outcome = await desk.dismiss_case(case.case_id, "officer_a", "no evidence", LOCATION_GOVERNMENT_COMPLEX, now_tick=1)

    assert outcome.kind == OutcomeKind.DISMISSED
    assert outcome.warrant_id is None
    stored_case = await store.get_case(case.case_id)
    assert stored_case is not None
    assert stored_case.status == CaseStatus.CLOSED_DISMISSED
    assert recorder.of_type(EVENT_CASE_DISMISSED)
