"""Part D: a government with something to do, and a court with a record.

`vote`, `petition`, `pray`, `file_complaint` and `register_org` have been
callable since genesis and were called **zero times** across the whole
series. Every report read that as apathy. It was not: there was no
`arrest`, no police, no verdict, and no tax bill to resent, so a vote
could not change anything a resident could feel.

NOTE ON THE TOOL TEXT. The addendum's section 12 says these descriptions
are written after the first full-day run with tax charged, because what
the first tax bill does to the town decides what they need to say. No
such run has happened, so the wording is provisional and these tests are
about the MECHANISMS, which are not.
"""

from __future__ import annotations

import pytest

from world.charter.models import GovernmentState
from world.core.clock import TICKS_PER_DAY
from world.tools.place_aliases import display_place
from world.tools.errors import (
    ActorRequirementError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.errors import ToolNotFoundError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.tools.models import CrimeCase, CrimeCaseKind
from world.tools.state import WorldState

from .conftest import spawn_agent

HALL = LOCATION_TOWN_HALL
COMPLEX = LOCATION_GOVERNMENT_COMPLEX
FIELD = "Kasukabe Farmland"


async def _seated(world: WorldState, agent_id: str = "pm", at: str = HALL) -> None:
    await spawn_agent(world, agent_id, at, household_id="h_holloway")
    world.agent_names[agent_id] = "Nell Holloway"
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id=agent_id, minister_agent_ids=[])
    )


def _case(world: WorldState, against: str, case_id: str = "c1") -> CrimeCase:
    case = CrimeCase(
        case_id=case_id,
        kind=CrimeCaseKind.THEFT,
        tick=world.tick(),
        filer_or_actor_agent_id="n09",
        against_agent_id=against,
        description="took four copper from the store",
    )
    world.crime_cases.append(case)
    return case


@pytest.mark.asyncio
async def test_a_seat_can_move_the_tax_rate(world: WorldState, registry) -> None:
    """`charter/government.py::set_tax_rate` has been complete the whole
    time and no tool called it, so the rate never moved off genesis."""
    await _seated(world, at=COMPLEX)

    outcome = await registry.execute(world, "pm", "set_tax_rate", {"tax_rate_bps": 250})

    assert outcome.result["tax_rate_bps"] == 250
    policy = await world.charter.store.get_policy()
    assert policy.tax_rate_bps == 250


@pytest.mark.asyncio
async def test_a_resident_with_no_seat_cannot_even_see_the_tax_rate_tool(
    world: WorldState, registry
) -> None:
    """DESIGN.md section 2: "Non-members cannot see a confidential tool
    exists." `set_tax_rate` is GOVERNMENT-namespace, so a resident without
    a seat gets the same answer it would get for a tool that does not
    exist - which is the point, not a worse error message."""
    await _seated(world, at=COMPLEX)
    await spawn_agent(world, "n09", COMPLEX)

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "n09", "set_tax_rate", {"tax_rate_bps": 0})


@pytest.mark.asyncio
async def test_an_election_can_have_a_candidate(world: WorldState, registry) -> None:
    """`vote` has been callable since genesis with nobody to vote for: the
    elections engine had a `declare_candidacy` and no tool reached it, so
    every ballot was empty by construction."""
    await spawn_agent(world, "n09", HALL, age_years=40.0, household_id="h_vance")
    election = await world.charter.elections.schedule_election(world.tick())

    outcome = await registry.execute(world, "n09", "declare_candidacy", {
        "election_id": election.election_id,
        "platform": "Plain accounts, and debts settled in the open.",
    })

    assert "n09" in outcome.result["candidates"]


@pytest.mark.asyncio
async def test_you_stand_at_the_town_hall(world: WorldState, registry) -> None:
    await spawn_agent(world, "n09", FIELD, age_years=40.0, household_id="h_vance")
    election = await world.charter.elections.schedule_election(world.tick())

    with pytest.raises(Exception):
        await registry.execute(world, "n09", "declare_candidacy", {
            "election_id": election.election_id,
            "platform": "Plain accounts, and debts settled in the open.",
        })


@pytest.mark.asyncio
async def test_convening_opens_a_real_conversation_and_gives_no_orders(
    world: WorldState, registry
) -> None:
    """ROADMAP Law 2 forbids an orchestrator passing messages. Atlas's
    group-session loop makes each member speak; that loop is exactly what
    this project decided not to port."""
    await _seated(world)

    outcome = await registry.execute(world, "pm", "convene", {
        "subject": "the head tax", "seats_only": True,
    })

    conversation_id = outcome.result["conversation_id"]
    record = world.conversations[conversation_id]
    assert record["topic"] == "the head tax"
    assert record["participants"][0] == "pm"
    assert record["place"] == HALL
    assert record["turns"] == 0


@pytest.mark.asyncio
async def test_convening_the_whole_town_reaches_everybody(
    world: WorldState, registry
) -> None:
    await _seated(world)
    await spawn_agent(world, "n09", FIELD)
    await spawn_agent(world, "n14", FIELD)

    outcome = await registry.execute(world, "pm", "convene", {
        "subject": "the head tax", "seats_only": False,
    })

    assert set(outcome.result["invited"]) == {"pm", "n09", "n14"}


@pytest.mark.asyncio
async def test_only_a_seat_convenes(world: WorldState, registry) -> None:
    await _seated(world)
    await spawn_agent(world, "n09", HALL)

    with pytest.raises(Exception):
        await registry.execute(world, "n09", "convene", {"subject": "anything"})


@pytest.mark.asyncio
async def test_only_police_arrest_and_a_seat_appoints_them(
    world: WorldState, registry
) -> None:
    await _seated(world)
    await spawn_agent(world, "n09", HALL)
    await spawn_agent(world, "n14", HALL)
    _case(world, against="n14")

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "n09", "arrest", {
            "agent_id": "n14", "case_id": "c1",
        })
    assert "appoint_police" in str(exc.value)

    await registry.execute(world, "pm", "appoint_police", {"agent_id": "n09"})
    outcome = await registry.execute(world, "n09", "arrest", {
        "agent_id": "n14", "case_id": "c1",
    })

    assert outcome.result["arrested"] == "n14"
    assert "n14" in world.confined


@pytest.mark.asyncio
async def test_an_arrest_needs_a_case_against_that_person(
    world: WorldState, registry
) -> None:
    """`steal` records ground truth for every theft, so once verdicts exist
    the false-conviction rate is a query. An arrest with no case behind it
    would make that query meaningless."""
    await _seated(world)
    await spawn_agent(world, "n09", HALL)
    await spawn_agent(world, "n14", HALL)
    await registry.execute(world, "pm", "appoint_police", {"agent_id": "n09"})

    with pytest.raises(NotFoundError):
        await registry.execute(world, "n09", "arrest", {
            "agent_id": "n14", "case_id": "no-such-case",
        })

    _case(world, against="pm")
    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n09", "arrest", {
            "agent_id": "n14", "case_id": "c1",
        })
    assert "accuses pm" in str(exc.value)


@pytest.mark.asyncio
async def test_an_arrest_actually_moves_them_to_the_cells(
    world: WorldState, registry
) -> None:
    """An arrest that leaves the accused standing in the field is a word."""
    await _seated(world)
    await spawn_agent(world, "n09", FIELD)
    await spawn_agent(world, "n14", FIELD)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=[])
    )
    world.police.add("n09")
    _case(world, against="n14")

    await registry.execute(world, "n09", "arrest", {"agent_id": "n14", "case_id": "c1"})

    assert world.location_of("n14") == display_place(HALL)


@pytest.mark.asyncio
async def test_a_confined_resident_cannot_walk_out_but_can_still_speak(
    world: WorldState, registry
) -> None:
    """Everything except `move`. Pleading, arguing and being argued with is
    the whole of what a cell is for."""
    await _seated(world)
    await spawn_agent(world, "n14", HALL)
    world.confined["n14"] = {"case_id": "c1", "since_tick": 0, "until_tick": None}

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "n14", "move", {"destination_location_id": FIELD})
    assert "held" in str(exc.value)

    await registry.execute(world, "n14", "say", {"content": "I did not take it."})


@pytest.mark.asyncio
async def test_a_fine_moves_money_and_closes_the_case(
    world: WorldState, registry
) -> None:
    await _seated(world)
    await spawn_agent(world, "n14", HALL, starting_sim=5_000)
    case = _case(world, against="n14")
    before = world.ledger.sim_balance("n14")

    outcome = await registry.execute(world, "pm", "rule", {
        "case_id": "c1", "verdict": "fine", "fine_sim": 400,
    })

    assert outcome.result["verdict"] == "fine"
    assert world.ledger.sim_balance("n14") == before - 400
    assert case.verdict == "fine"
    assert case.outcome is None


@pytest.mark.asyncio
async def test_a_jail_sentence_has_an_end(world: WorldState, registry) -> None:
    await _seated(world)
    await spawn_agent(world, "n14", HALL)
    _case(world, against="n14")
    world.confined["n14"] = {"case_id": "c1", "since_tick": 0, "until_tick": None}

    await registry.execute(world, "pm", "rule", {
        "case_id": "c1", "verdict": "jail", "jail_days": 2,
    })

    assert world.confined["n14"]["until_tick"] == world.tick() + 2 * TICKS_PER_DAY


@pytest.mark.asyncio
async def test_a_dismissal_ends_the_confinement(world: WorldState, registry) -> None:
    await _seated(world)
    await spawn_agent(world, "n14", HALL)
    _case(world, against="n14")
    world.confined["n14"] = {"case_id": "c1", "since_tick": 0, "until_tick": None}

    await registry.execute(world, "pm", "rule", {"case_id": "c1", "verdict": "dismiss"})

    assert "n14" not in world.confined


@pytest.mark.asyncio
async def test_one_verdict_per_case(world: WorldState, registry) -> None:
    """A case that can be ruled on twice is a case with no verdict."""
    await _seated(world)
    await spawn_agent(world, "n14", HALL, starting_sim=5_000)
    _case(world, against="n14")
    await registry.execute(world, "pm", "rule", {
        "case_id": "c1", "verdict": "fine", "fine_sim": 100,
    })

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "pm", "rule", {
            "case_id": "c1", "verdict": "dismiss",
        })

    assert "already ruled" in str(exc.value)


@pytest.mark.asyncio
async def test_a_verdict_nobody_recognises_is_refused_with_the_list(
    world: WorldState, registry
) -> None:
    await _seated(world)
    await spawn_agent(world, "n14", HALL)
    _case(world, against="n14")

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "pm", "rule", {
            "case_id": "c1", "verdict": "banish",
        })

    assert "fine" in str(exc.value) and "dismiss" in str(exc.value)


@pytest.mark.asyncio
async def test_release_frees_them_and_says_how_long_they_were_held(
    world: WorldState, registry
) -> None:
    await _seated(world)
    await spawn_agent(world, "n14", HALL)
    world.confined["n14"] = {"case_id": "c1", "since_tick": 0, "until_tick": None}
    world.current_tick = 30

    outcome = await registry.execute(world, "pm", "release", {"agent_id": "n14"})

    assert outcome.result["released"] == "n14"
    assert "n14" not in world.confined
    freed = [e for e in outcome.events if e.type == "crime.release.recorded"][0]
    assert freed.payload["ticks_held"] == 30
    await registry.execute(world, "n14", "move", {"destination_location_id": FIELD})


@pytest.mark.asyncio
async def test_releasing_somebody_who_is_not_held(world: WorldState, registry) -> None:
    await _seated(world)
    await spawn_agent(world, "n14", HALL)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "pm", "release", {"agent_id": "n14"})

    assert "not being held" in str(exc.value)


@pytest.mark.asyncio
async def test_the_jail_survives_a_checkpoint(world: WorldState, registry) -> None:
    """A resume that forgot who was in the cells would release the whole
    jail without anybody deciding to."""
    await _seated(world)
    await spawn_agent(world, "n09", HALL)
    await spawn_agent(world, "n14", HALL)
    world.police.add("n09")
    world.confined["n14"] = {"case_id": "c1", "since_tick": 4, "until_tick": None}

    saved = world.snapshot()
    world.police.clear()
    world.confined.clear()
    world.restore(saved)

    assert world.police == {"n09"}
    assert world.confined["n14"]["case_id"] == "c1"


@pytest.mark.asyncio
async def test_the_town_starts_with_no_police_and_an_empty_jail(
    world: WorldState, registry
) -> None:
    """DESIGN.md gives the town a government and no police force. A run in
    which somebody is appointed is a run in which somebody decided to."""
    assert world.police == set()
    assert world.confined == {}
