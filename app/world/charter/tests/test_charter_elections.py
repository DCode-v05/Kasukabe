"""Tests for world/charter/elections.py: candidacy, campaign period,
in-person voting at town hall, tally, and transfer of power.
"""

from __future__ import annotations

import pytest

from world.charter.elections import ElectionEngine
from world.charter.errors import (
    AlreadyDeclaredCandidacy,
    AlreadyVoted,
    ElectionPhaseError,
    NotAtRequiredLocation,
    NotEligibleToVote,
    TermLimitExceeded,
)
from world.charter.events import EVENT_REGIME_CHANGED
from world.charter.genesis import seed_genesis
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_TOWN_HALL,
    Charter,
    ElectionPhase,
    EligibilityFacts,
    GovernmentState,
)
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder


def adult(agent_id: str) -> EligibilityFacts:
    return EligibilityFacts(agent_id=agent_id, age_years=30, is_resident=True)


async def make_engine(recorder: EventRecorder | None = None) -> tuple[ElectionEngine, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    return ElectionEngine(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_schedule_election_derives_windows_from_charter() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    # Genesis defaults: 1 day candidacy + 1 day campaign + 1 day voting,
    # 144 ticks/day (DESIGN.md section 1).
    assert election.candidacy_opens_tick == 0
    assert election.candidacy_closes_tick == 144
    assert election.voting_opens_tick == 288
    assert election.voting_closes_tick == 432
    assert election.phase == ElectionPhase.SCHEDULED
    assert election.seats_at_scheduling == 5


@pytest.mark.asyncio
async def test_declare_candidacy_before_window_opens_fails() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=10)  # opens at tick 10

    with pytest.raises(ElectionPhaseError):
        await engine.declare_candidacy(
            election.election_id,
            "agent-1",
            adult("agent-1"),
            now_tick=5,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_declare_candidacy_requires_town_hall() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    with pytest.raises(NotAtRequiredLocation):
        await engine.declare_candidacy(
            election.election_id,
            "agent-1",
            adult("agent-1"),
            now_tick=0,
            location=LOCATION_GOVERNMENT_COMPLEX,
        )


@pytest.mark.asyncio
async def test_declare_candidacy_succeeds_in_open_window() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    updated = await engine.declare_candidacy(
        election.election_id, "agent-1", adult("agent-1"), now_tick=0, location=LOCATION_TOWN_HALL
    )
    assert "agent-1" in updated.candidates
    assert updated.phase == ElectionPhase.CANDIDACY_OPEN


@pytest.mark.asyncio
async def test_declare_candidacy_twice_rejected() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)
    await engine.declare_candidacy(
        election.election_id, "agent-1", adult("agent-1"), now_tick=0, location=LOCATION_TOWN_HALL
    )

    with pytest.raises(AlreadyDeclaredCandidacy):
        await engine.declare_candidacy(
            election.election_id,
            "agent-1",
            adult("agent-1"),
            now_tick=0,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_declare_candidacy_after_window_closes_fails() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    with pytest.raises(ElectionPhaseError):
        await engine.declare_candidacy(
            election.election_id,
            "agent-1",
            adult("agent-1"),
            now_tick=200,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_declare_candidacy_rejects_agent_below_suffrage_age() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)
    minor = EligibilityFacts(agent_id="agent-young", age_years=12, is_resident=True)

    with pytest.raises(NotEligibleToVote):
        await engine.declare_candidacy(
            election.election_id, "agent-young", minor, now_tick=0, location=LOCATION_TOWN_HALL
        )


@pytest.mark.asyncio
async def test_declare_candidacy_rejects_non_resident_when_residency_required() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)
    visitor = EligibilityFacts(agent_id="agent-visitor", age_years=30, is_resident=False)

    with pytest.raises(NotEligibleToVote):
        await engine.declare_candidacy(
            election.election_id, "agent-visitor", visitor, now_tick=0, location=LOCATION_TOWN_HALL
        )


@pytest.mark.asyncio
async def test_declare_candidacy_rejects_agent_at_term_limit() -> None:
    store = CharterStore()
    await seed_genesis(store, genesis_tick=0)
    await store.push_charter(Charter(version=1, term_limit_terms=1))
    await store.set_government_state(
        GovernmentState(pm_agent_id="agent-1", terms_served={"agent-1": 1})
    )
    engine = ElectionEngine(store)
    election = await engine.schedule_election(now_tick=0)

    with pytest.raises(TermLimitExceeded):
        await engine.declare_candidacy(
            election.election_id,
            "agent-1",
            adult("agent-1"),
            now_tick=0,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_advance_phase_progresses_through_schedule() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    assert (
        await engine.advance_phase(election.election_id, now_tick=0)
    ).phase == ElectionPhase.CANDIDACY_OPEN
    assert (
        await engine.advance_phase(election.election_id, now_tick=150)
    ).phase == ElectionPhase.CAMPAIGNING
    assert (
        await engine.advance_phase(election.election_id, now_tick=300)
    ).phase == ElectionPhase.VOTING_OPEN


@pytest.mark.asyncio
async def test_advance_phase_never_reaches_tallied() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    result = await engine.advance_phase(election.election_id, now_tick=10_000)
    assert result.phase == ElectionPhase.VOTING_OPEN


@pytest.mark.asyncio
async def test_cast_vote_requires_town_hall() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)
    await engine.declare_candidacy(
        election.election_id, "agent-1", adult("agent-1"), now_tick=0, location=LOCATION_TOWN_HALL
    )

    with pytest.raises(NotAtRequiredLocation):
        await engine.cast_vote(
            election.election_id,
            "agent-2",
            "agent-1",
            adult("agent-2"),
            now_tick=300,
            location=LOCATION_GOVERNMENT_COMPLEX,
        )


@pytest.mark.asyncio
async def test_cast_vote_before_polls_open_fails() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)
    await engine.declare_candidacy(
        election.election_id, "agent-1", adult("agent-1"), now_tick=0, location=LOCATION_TOWN_HALL
    )

    with pytest.raises(ElectionPhaseError):
        await engine.cast_vote(
            election.election_id,
            "agent-2",
            "agent-1",
            adult("agent-2"),
            now_tick=150,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_cast_vote_for_undeclared_candidate_fails() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)

    with pytest.raises(ElectionPhaseError):
        await engine.cast_vote(
            election.election_id,
            "agent-2",
            "nobody-declared",
            adult("agent-2"),
            now_tick=300,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_cast_vote_twice_rejected() -> None:
    engine, _ = await make_engine()
    election = await engine.schedule_election(now_tick=0)
    await engine.declare_candidacy(
        election.election_id, "agent-1", adult("agent-1"), now_tick=0, location=LOCATION_TOWN_HALL
    )
    await engine.cast_vote(
        election.election_id,
        "agent-2",
        "agent-1",
        adult("agent-2"),
        now_tick=300,
        location=LOCATION_TOWN_HALL,
    )

    with pytest.raises(AlreadyVoted):
        await engine.cast_vote(
            election.election_id,
            "agent-2",
            "agent-1",
            adult("agent-2"),
            now_tick=310,
            location=LOCATION_TOWN_HALL,
        )


@pytest.mark.asyncio
async def test_cast_vote_ballot_is_not_broadcast_in_event_payload() -> None:
    recorder = EventRecorder()
    engine, _ = await make_engine(recorder)
    election = await engine.schedule_election(now_tick=0)
    await engine.declare_candidacy(
        election.election_id, "agent-1", adult("agent-1"), now_tick=0, location=LOCATION_TOWN_HALL
    )
    await engine.cast_vote(
        election.election_id,
        "agent-2",
        "agent-1",
        adult("agent-2"),
        now_tick=300,
        location=LOCATION_TOWN_HALL,
    )

    vote_events = recorder.of_type("politics.vote.cast")
    assert len(vote_events) == 1
    assert "candidate_agent_id" not in vote_events[0]["payload"]
    assert vote_events[0]["payload"]["voter_agent_id"] == "agent-2"


async def run_full_election(
    engine: ElectionEngine, candidates: list[str], ballots: dict[str, str], now_tick_offset: int = 0
):
    election = await engine.schedule_election(now_tick=now_tick_offset)
    for agent_id in candidates:
        await engine.declare_candidacy(
            election.election_id,
            agent_id,
            adult(agent_id),
            now_tick=now_tick_offset,
            location=LOCATION_TOWN_HALL,
        )
    for voter_agent_id, candidate_agent_id in ballots.items():
        await engine.cast_vote(
            election.election_id,
            voter_agent_id,
            candidate_agent_id,
            adult(voter_agent_id),
            now_tick=now_tick_offset + 300,
            location=LOCATION_TOWN_HALL,
        )
    return await engine.tally(election.election_id, now_tick=now_tick_offset + 500)


@pytest.mark.asyncio
async def test_tally_elects_pm_and_ministers_by_vote_count() -> None:
    engine, store = await make_engine()
    ballots = {
        "v1": "candidate-a",
        "v2": "candidate-a",
        "v3": "candidate-a",
        "v4": "candidate-b",
        "v5": "candidate-b",
        "v6": "candidate-c",
    }
    election = await run_full_election(
        engine, ["candidate-a", "candidate-b", "candidate-c"], ballots
    )

    assert election.phase == ElectionPhase.TALLIED
    assert election.result is not None
    assert election.result.pm_agent_id == "candidate-a"
    assert set(election.result.minister_agent_ids) == {"candidate-b", "candidate-c"}

    state = await store.get_government_state()
    assert state.pm_agent_id == "candidate-a"


@pytest.mark.asyncio
async def test_tally_with_no_candidates_leaves_government_vacant() -> None:
    engine, store = await make_engine()
    election = await run_full_election(engine, [], {})

    assert election.result is not None
    assert election.result.pm_agent_id is None
    assert election.result.minister_agent_ids == []
    state = await store.get_government_state()
    assert state.pm_agent_id is None


@pytest.mark.asyncio
async def test_tally_calling_twice_is_idempotent() -> None:
    engine, _ = await make_engine()
    election = await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"})
    first_result = election.result

    again = await engine.tally(election.election_id, now_tick=999_999)
    assert again.result == first_result


@pytest.mark.asyncio
async def test_regime_changed_event_emitted_when_pm_changes() -> None:
    recorder = EventRecorder()
    engine, _ = await make_engine(recorder)
    await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"})

    assert recorder.of_type(EVENT_REGIME_CHANGED)


@pytest.mark.asyncio
async def test_regime_changed_not_emitted_when_same_pm_reelected() -> None:
    recorder = EventRecorder()
    engine, _ = await make_engine(recorder)
    await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"}, now_tick_offset=0)
    recorder.events.clear()

    await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"}, now_tick_offset=1000)
    assert recorder.of_type(EVENT_REGIME_CHANGED) == []


@pytest.mark.asyncio
async def test_unlimited_re_election_allows_same_agent_to_win_repeatedly() -> None:
    engine, store = await make_engine()
    await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"}, now_tick_offset=0)
    await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"}, now_tick_offset=1000)
    await run_full_election(engine, ["candidate-a"], {"v1": "candidate-a"}, now_tick_offset=2000)

    state = await store.get_government_state()
    assert state.pm_agent_id == "candidate-a"
    assert state.terms_served["candidate-a"] == 3
