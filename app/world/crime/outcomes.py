"""Case outcomes (DESIGN.md section 6: "fine or jail (confinement, no
income)").

Both punitive outcomes require a GRANTED `Warrant` naming the convicted
agent for the same case - the minister's signature (warrants.py) is the
judicial authorization, DESIGN.md's pipeline is a straight line
("investigate -> minister-signed warrant -> fine or jail"), not
"warrant -> a second minister decision -> fine or jail", so applying the
outcome itself is a police action here (`GovernmentPort.is_police_officer`,
not `is_minister`), executing what the warrant already authorized.
Dismissal doesn't punish anyone and needs no warrant.

`jail_hours` is caller-specified (the task's own phrase, "confinement N
game-hours" - DESIGN.md does not pin N, the officer decides a sentence
length case by case), converted to a tick count via
`models.hours_to_ticks`. "No income" and "tools locked to jail location"
are enforced by callers outside this package (payroll code and the tool
dispatcher's location gate, neither of which exists yet) consulting
`confinement_status` / `tools_locked_to` before acting - this module
owns the query surface, not the payroll or dispatcher wiring, matching
app/CONVENTIONS.md's module boundaries (crime/ can't reach into a payroll
loop that isn't crime/'s to own).
"""

from __future__ import annotations

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.crime.errors import (
    AlreadyConfined,
    CaseAlreadyClosed,
    CaseNotFound,
    InvalidCrimeAmount,
    NotAtRequiredLocation,
    NotPoliceOfficer,
    WarrantNotFound,
    WarrantNotGranted,
)
from world.crime.events import (
    EVENT_CASE_DISMISSED,
    EVENT_CONFINEMENT_RELEASED,
    EVENT_FINE_IMPOSED,
    EVENT_JAIL_IMPOSED,
    EventSink,
    build_event,
    noop_sink,
)
from world.crime.models import (
    CLOSED_CASE_STATUSES,
    CaseFile,
    CaseOutcome,
    CaseStatus,
    ConfinementRecord,
    OutcomeKind,
    Warrant,
    WarrantStatus,
    hours_to_ticks,
)
from world.crime.ports import EconomyPort, GovernmentPort
from world.crime.store import CrimeStore


class OutcomeDesk:
    def __init__(
        self,
        store: CrimeStore,
        economy: EconomyPort,
        government: GovernmentPort,
        event_sink: EventSink | None = None,
    ) -> None:
        self.store = store
        self.economy = economy
        self.government = government
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def require_location(self, location: str) -> None:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)

    async def require_officer(self, agent_id: str) -> None:
        if not await self.government.is_police_officer(agent_id):
            raise NotPoliceOfficer(agent_id)

    async def require_open_case(self, case_id: str) -> CaseFile:
        case = await self.store.get_case(case_id)
        if case is None:
            raise CaseNotFound(case_id)
        if case.status in CLOSED_CASE_STATUSES:
            raise CaseAlreadyClosed(case_id, case.status.value)
        return case

    async def require_granted_warrant(self, case_id: str, warrant_id: str) -> Warrant:
        warrant = await self.store.get_warrant(warrant_id)
        if warrant is None:
            raise WarrantNotFound(warrant_id)
        if warrant.case_id != case_id:
            raise WarrantNotGranted(warrant_id, f"belongs to case {warrant.case_id!r}, not {case_id!r}")
        if warrant.status is not WarrantStatus.GRANTED:
            raise WarrantNotGranted(warrant_id, f"status is {warrant.status.value!r}, not granted")
        return warrant

    async def impose_fine(
        self,
        case_id: str,
        warrant_id: str,
        officer_agent_id: str,
        fine_sim: int,
        location: str,
        now_tick: int,
    ) -> CaseOutcome:
        await self.require_officer(officer_agent_id)
        await self.require_location(location)
        if fine_sim <= 0:
            raise InvalidCrimeAmount(fine_sim)

        case = await self.require_open_case(case_id)
        warrant = await self.require_granted_warrant(case_id, warrant_id)

        await self.economy.collect_fine(
            warrant.target_agent_id, fine_sim, now_tick, memo=f"fine for case {case_id}"
        )

        outcome = CaseOutcome(
            case_id=case_id,
            warrant_id=warrant_id,
            kind=OutcomeKind.FINE,
            convicted_agent_id=warrant.target_agent_id,
            fine_sim=fine_sim,
            authorized_by_minister_agent_id=warrant.decided_by_minister_agent_id,
            executed_by_officer_agent_id=officer_agent_id,
            decided_tick=now_tick,
        )
        await self.store.append_outcome(outcome)

        case.status = CaseStatus.CLOSED_FINE
        case.closed_tick = now_tick
        await self.store.put_case(case)

        await self.event_sink(
            build_event(
                EVENT_FINE_IMPOSED,
                {
                    "outcome_id": outcome.outcome_id,
                    "case_id": case_id,
                    "location_id": location,
                    "target_agent_id": warrant.target_agent_id,
                    "involved_agent_ids": [officer_agent_id, warrant.target_agent_id],
                    "fine_sim": fine_sim,
                },
            )
        )
        return outcome

    async def impose_jail(
        self,
        case_id: str,
        warrant_id: str,
        officer_agent_id: str,
        jail_hours: float,
        jail_location_id: str,
        location: str,
        now_tick: int,
    ) -> tuple[CaseOutcome, ConfinementRecord]:
        await self.require_officer(officer_agent_id)
        await self.require_location(location)
        if jail_hours <= 0:
            raise InvalidCrimeAmount(int(jail_hours))

        case = await self.require_open_case(case_id)
        warrant = await self.require_granted_warrant(case_id, warrant_id)

        existing_confinement = await self.store.get_active_confinement(warrant.target_agent_id)
        if existing_confinement is not None:
            raise AlreadyConfined(warrant.target_agent_id)

        outcome = CaseOutcome(
            case_id=case_id,
            warrant_id=warrant_id,
            kind=OutcomeKind.JAIL,
            convicted_agent_id=warrant.target_agent_id,
            jail_hours=jail_hours,
            jail_location_id=jail_location_id,
            authorized_by_minister_agent_id=warrant.decided_by_minister_agent_id,
            executed_by_officer_agent_id=officer_agent_id,
            decided_tick=now_tick,
        )
        await self.store.append_outcome(outcome)

        confinement = ConfinementRecord(
            agent_id=warrant.target_agent_id,
            case_id=case_id,
            start_tick=now_tick,
            release_tick=now_tick + hours_to_ticks(jail_hours),
            jail_location_id=jail_location_id,
        )
        await self.store.put_confinement(confinement)

        case.status = CaseStatus.CLOSED_JAIL
        case.closed_tick = now_tick
        await self.store.put_case(case)

        await self.event_sink(
            build_event(
                EVENT_JAIL_IMPOSED,
                {
                    "outcome_id": outcome.outcome_id,
                    "confinement_id": confinement.confinement_id,
                    "case_id": case_id,
                    "location_id": location,
                    "target_agent_id": warrant.target_agent_id,
                    "involved_agent_ids": [officer_agent_id, warrant.target_agent_id],
                    "jail_hours": jail_hours,
                    "jail_location_id": jail_location_id,
                    "release_tick": confinement.release_tick,
                },
            )
        )
        return outcome, confinement

    async def dismiss_case(self, case_id: str, officer_agent_id: str, reason: str, location: str, now_tick: int) -> CaseOutcome:
        await self.require_officer(officer_agent_id)
        await self.require_location(location)
        case = await self.require_open_case(case_id)

        outcome = CaseOutcome(
            case_id=case_id,
            warrant_id=None,
            kind=OutcomeKind.DISMISSED,
            convicted_agent_id=None,
            authorized_by_minister_agent_id=None,
            executed_by_officer_agent_id=officer_agent_id,
            decided_tick=now_tick,
        )
        await self.store.append_outcome(outcome)

        case.status = CaseStatus.CLOSED_DISMISSED
        case.closed_tick = now_tick
        await self.store.put_case(case)

        await self.event_sink(
            build_event(
                EVENT_CASE_DISMISSED,
                {
                    "outcome_id": outcome.outcome_id,
                    "case_id": case_id,
                    "location_id": location,
                    "involved_agent_ids": [officer_agent_id],
                    "reason": reason,
                },
            )
        )
        return outcome

    # -- confinement query surface (jail/payroll/dispatcher integration) --

    async def confinement_status(self, agent_id: str) -> ConfinementRecord | None:
        """Pure read: the active confinement for this agent, if any,
        without auto-releasing an expired one - `release_due_confinements`
        is the one place that mutates release state."""
        return await self.store.get_active_confinement(agent_id)

    async def tools_locked_to(self, agent_id: str) -> str | None:
        """The one location a confined agent's tools should be allowed to
        run from, or None if the agent isn't confined. Callers (the
        future world/ tool dispatcher) are expected to check this before
        executing any location-gated tool call for `agent_id`, and payroll
        code is expected to check it (or `confinement_status`) before
        paying a wage - jail's "no income" rule (DESIGN.md section 6) has
        no other enforcement point, crime/ does not own the payroll loop.
        """
        confinement = await self.store.get_active_confinement(agent_id)
        return confinement.jail_location_id if confinement is not None else None

    async def release_due_confinements(self, now_tick: int) -> list[ConfinementRecord]:
        """Tick-driven sweep: releases every active confinement whose
        sentence has run out. Mirrors life/health.py's
        onset-then-advance_recovery shape - a caller (world's tick loop)
        is expected to call this once per tick alongside the rest of the
        world's periodic advancement."""
        released: list[ConfinementRecord] = []
        for record in await self.store.list_active_confinements():
            if now_tick >= record.release_tick:
                record.released = True
                await self.store.put_confinement(record)
                released.append(record)
                await self.event_sink(
                    build_event(
                        EVENT_CONFINEMENT_RELEASED,
                        {
                            "confinement_id": record.confinement_id,
                            "case_id": record.case_id,
                            "location_id": record.jail_location_id,
                            "target_agent_id": record.agent_id,
                            "involved_agent_ids": [record.agent_id],
                        },
                    )
                )
        return released
