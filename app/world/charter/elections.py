"""Election engine: candidacy, campaign, in-person voting, tally, transfer
of power (DESIGN.md section 7).

Time is expressed in ticks throughout, not wall clock: DESIGN.md's whole
world runs on a 10-game-minute tick, and an election's schedule
(candidacy window, campaign period, voting window) is derived once, at
`schedule_election` time, from whatever `Charter` durations are in force
then. A later amendment to `election_interval_days` or the window
durations only affects elections scheduled after the amendment, not one
already in flight - changing the rules of an election that is already
running would be a much stranger kind of "authoritarianism must be
reachable" than DESIGN.md intends.

Phase progression (`SCHEDULED -> CANDIDACY_OPEN -> CAMPAIGNING ->
VOTING_OPEN`) is tick-driven and idempotent: `advance_phase` (and the
internal catch-up it shares with `declare_candidacy`/`cast_vote`) computes
the phase a fully-caught-up election would be in for the given tick and
moves forward to it, so it does not matter whether the world tick loop
calls `advance_phase` every tick or only occasionally. The final step,
`VOTING_OPEN -> TALLIED`, is not part of that automatic progression: tally
has real side effects (transfer of power, terms_served bookkeeping,
scheduling the next election) and is only ever taken by an explicit
`tally()` call, once, by whichever caller closes the polls.
"""

from __future__ import annotations

from world.charter.errors import (
    AlreadyDeclaredCandidacy,
    AlreadyVoted,
    ElectionNotFound,
    ElectionPhaseError,
    NotAtRequiredLocation,
    NotEligibleToVote,
    TermLimitExceeded,
)
from world.charter.events import (
    EVENT_CANDIDACY_DECLARED,
    EVENT_ELECTION_PHASE_ADVANCED,
    EVENT_ELECTION_SCHEDULED,
    EVENT_ELECTION_TALLIED,
    EVENT_REGIME_CHANGED,
    EVENT_VOTE_CAST,
    EventSink,
    build_event,
    noop_sink,
)
from world.charter.models import (
    LOCATION_TOWN_HALL,
    Candidacy,
    Charter,
    Election,
    ElectionPhase,
    ElectionResult,
    EligibilityFacts,
    GovernmentState,
    days_to_ticks,
)
from world.charter.store import CharterStore


def check_suffrage(charter: Charter, facts: EligibilityFacts) -> None:
    """Raise `NotEligibleToVote` if `facts` fails the charter's suffrage
    rules. Shared by candidacy declaration and voting - DESIGN.md doesn't
    describe a separate, looser bar for who may run versus who may vote,
    so a candidate must clear the same suffrage rule as a voter.
    """
    if facts.age_years < charter.suffrage_min_age:
        raise NotEligibleToVote(
            facts.agent_id,
            f"age {facts.age_years} is below suffrage_min_age {charter.suffrage_min_age}",
        )
    if charter.suffrage_requires_residency and not facts.is_resident:
        raise NotEligibleToVote(
            facts.agent_id, "charter requires residency and agent is not a resident"
        )


def compute_phase_for_tick(election: Election, now_tick: int) -> ElectionPhase:
    """Pure function: what phase would a fully caught-up election be in at
    `now_tick`? Never returns TALLIED - that transition only happens
    through an explicit `tally()` call, see module docstring.
    """
    if now_tick < election.candidacy_opens_tick:
        return ElectionPhase.SCHEDULED
    if now_tick < election.candidacy_closes_tick:
        return ElectionPhase.CANDIDACY_OPEN
    if now_tick < election.voting_opens_tick:
        return ElectionPhase.CAMPAIGNING
    return ElectionPhase.VOTING_OPEN


def compute_tally(election: Election) -> tuple[dict[str, int], list[str]]:
    """Pure function: count ballots and rank candidates.

    Returns `(vote_counts, ranked_agent_ids)`. Every declared candidate
    appears in `vote_counts`, including with zero votes, so a research
    query over "how many votes did every candidate get" never has to
    cross-reference the candidate list separately. Ranking is (votes
    descending, agent_id ascending) - the agent_id tie-break is a
    deliberate, documented simplification: real tie-break mechanisms
    (runoff, random draw) are exactly the kind of "further checks and
    balances they must invent" DESIGN.md section 7 leaves to the town,
    and a fixed deterministic order keeps a tied election reproducible
    for research replay instead of depending on hidden randomness.

    Splitting the ranked list into PM/ministers is the caller's job
    (`ElectionEngine.tally`), since that split needs the live charter's
    `pm_seats`/`minister_seats`, which this pure function deliberately
    doesn't take a dependency on.
    """
    vote_counts: dict[str, int] = {agent_id: 0 for agent_id in election.candidates}
    for candidate_agent_id in election.ballots.values():
        vote_counts[candidate_agent_id] = vote_counts.get(candidate_agent_id, 0) + 1

    ranked = sorted(vote_counts.keys(), key=lambda agent_id: (-vote_counts[agent_id], agent_id))
    return vote_counts, ranked


class ElectionEngine:
    """Stateful orchestration around the pure functions above: loads from
    `CharterStore`, validates, mutates, persists, and emits events.
    """

    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def schedule_election(self, now_tick: int) -> Election:
        charter = await self.store.get_charter()
        candidacy_opens = now_tick
        candidacy_closes = candidacy_opens + days_to_ticks(charter.candidacy_window_days)
        voting_opens = candidacy_closes + days_to_ticks(charter.campaign_period_days)
        voting_closes = voting_opens + days_to_ticks(charter.voting_window_days)
        election = Election(
            charter_version_at_scheduling=charter.version,
            seats_at_scheduling=charter.total_seats,
            scheduled_tick=now_tick,
            candidacy_opens_tick=candidacy_opens,
            candidacy_closes_tick=candidacy_closes,
            voting_opens_tick=voting_opens,
            voting_closes_tick=voting_closes,
        )
        await self.store.put_election(election)
        await self.event_sink(
            build_event(
                EVENT_ELECTION_SCHEDULED,
                {
                    "election_id": election.election_id,
                    "candidacy_opens_tick": candidacy_opens,
                    "candidacy_closes_tick": candidacy_closes,
                    "voting_opens_tick": voting_opens,
                    "voting_closes_tick": voting_closes,
                },
            )
        )
        return election

    async def load_election(self, election_id: str) -> Election:
        election = await self.store.get_election(election_id)
        if election is None:
            raise ElectionNotFound(election_id)
        return election

    async def advance_phase(self, election_id: str, now_tick: int) -> Election:
        election = await self.load_election(election_id)
        target = compute_phase_for_tick(election, now_tick)
        if target == election.phase or election.phase == ElectionPhase.TALLIED:
            return election
        election.phase = target
        await self.store.put_election(election)
        await self.event_sink(
            build_event(
                EVENT_ELECTION_PHASE_ADVANCED,
                {"election_id": election_id, "phase": target.value, "tick": now_tick},
            )
        )
        return election

    async def declare_candidacy(
        self,
        election_id: str,
        agent_id: str,
        facts: EligibilityFacts,
        now_tick: int,
        location: str,
        because: str = "",
    ) -> Election:
        if location != LOCATION_TOWN_HALL:
            raise NotAtRequiredLocation(LOCATION_TOWN_HALL, location)
        election = await self.advance_phase(election_id, now_tick)
        if election.phase != ElectionPhase.CANDIDACY_OPEN:
            raise ElectionPhaseError(
                election_id, ElectionPhase.CANDIDACY_OPEN.value, election.phase.value
            )
        if agent_id in election.candidates:
            raise AlreadyDeclaredCandidacy(election_id, agent_id)

        charter = await self.store.get_charter()
        check_suffrage(charter, facts)
        government_state = await self.store.get_government_state()
        terms_served = government_state.terms_served.get(agent_id, 0)
        if charter.term_limit_terms is not None and terms_served >= charter.term_limit_terms:
            raise TermLimitExceeded(agent_id, terms_served, charter.term_limit_terms)

        election.candidates[agent_id] = Candidacy(agent_id=agent_id, declared_tick=now_tick)
        await self.store.put_election(election)
        await self.event_sink(
            build_event(
                EVENT_CANDIDACY_DECLARED,
                {
                    "election_id": election_id,
                    "agent_id": agent_id,
                    "because": because,
                    "involved_agent_ids": [agent_id],
                },
            )
        )
        return election

    async def cast_vote(
        self,
        election_id: str,
        voter_agent_id: str,
        candidate_agent_id: str,
        facts: EligibilityFacts,
        now_tick: int,
        location: str,
        because: str = "",
    ) -> Election:
        if location != LOCATION_TOWN_HALL:
            raise NotAtRequiredLocation(LOCATION_TOWN_HALL, location)
        election = await self.advance_phase(election_id, now_tick)
        if election.phase != ElectionPhase.VOTING_OPEN:
            raise ElectionPhaseError(
                election_id, ElectionPhase.VOTING_OPEN.value, election.phase.value
            )
        if voter_agent_id in election.ballots:
            raise AlreadyVoted(election_id, voter_agent_id)
        if candidate_agent_id not in election.candidates:
            raise ElectionPhaseError(election_id, "a declared candidate", "an undeclared agent_id")

        charter = await self.store.get_charter()
        check_suffrage(charter, facts)

        election.ballots[voter_agent_id] = candidate_agent_id
        await self.store.put_election(election)
        await self.event_sink(
            build_event(
                EVENT_VOTE_CAST,
                {
                    "election_id": election_id,
                    "voter_agent_id": voter_agent_id,
                    "because": because,
                },
            )
        )
        return election

    async def tally(self, election_id: str, now_tick: int) -> Election:
        election = await self.load_election(election_id)
        if election.phase == ElectionPhase.TALLIED:
            return election
        if election.phase != ElectionPhase.VOTING_OPEN:
            election = await self.advance_phase(election_id, now_tick)
        if election.phase != ElectionPhase.VOTING_OPEN:
            raise ElectionPhaseError(
                election_id, ElectionPhase.VOTING_OPEN.value, election.phase.value
            )

        charter = await self.store.get_charter()
        vote_counts, ranked = compute_tally(election)
        has_pm_seat = charter.pm_seats >= 1

        pm_agent_id: str | None = None
        ministers: list[str] = []
        if has_pm_seat and ranked:
            pm_agent_id = ranked[0]
            ministers = ranked[1 : 1 + charter.minister_seats]
        else:
            ministers = ranked[: charter.minister_seats]

        result = ElectionResult(
            pm_agent_id=pm_agent_id,
            minister_agent_ids=ministers,
            vote_counts=vote_counts,
            tallied_tick=now_tick,
        )
        election.phase = ElectionPhase.TALLIED
        election.result = result
        await self.store.put_election(election)

        previous_state = await self.store.get_government_state()
        regime_changed = previous_state.pm_agent_id != pm_agent_id
        terms_served = dict(previous_state.terms_served)
        for winner_agent_id in ([pm_agent_id] if pm_agent_id is not None else []) + ministers:
            terms_served[winner_agent_id] = terms_served.get(winner_agent_id, 0) + 1
        new_state = GovernmentState(
            pm_agent_id=pm_agent_id, minister_agent_ids=ministers, terms_served=terms_served
        )
        await self.store.set_government_state(new_state)

        await self.event_sink(
            build_event(
                EVENT_ELECTION_TALLIED,
                {
                    "election_id": election_id,
                    "pm_agent_id": pm_agent_id,
                    "minister_agent_ids": ministers,
                    "vote_counts": vote_counts,
                },
            )
        )
        if regime_changed:
            await self.event_sink(
                build_event(
                    EVENT_REGIME_CHANGED,
                    {
                        "previous_pm_agent_id": previous_state.pm_agent_id,
                        "new_pm_agent_id": pm_agent_id,
                    },
                )
            )
        return election
