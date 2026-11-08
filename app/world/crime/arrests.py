"""The `arrest` primitive (DESIGN.md section 2 primitive list: "arrest
(police only)").

Co-located, per the task's own framing and DESIGN.md section 2's blanket
"every tool is location-gated" rule: an officer can only arrest someone
standing where they are, there is no remote-arrest primitive. Unlike
warrants.py's bureaucratic actions, arrest has no fixed-building
requirement - officers make arrests wherever the suspect is, not at the
government complex.

Does not itself require a warrant (see models.py's `ArrestRecord`
docstring): DESIGN.md's primitive list gates arrest to "police only", not
to "police with a signed warrant", so an officer may act on probable
cause. When a warrant already exists for the same case and target it can
be cited (`warrant_id`) for the audit trail; outcomes.py is the module
that actually requires one, at sentencing, not here.
"""

from __future__ import annotations

from world.crime.errors import (
    CaseAlreadyClosed,
    CaseNotFound,
    NotPoliceOfficer,
    WarrantNotFound,
    WarrantNotGranted,
)
from world.crime.events import EVENT_ARREST_MADE, EventSink, build_event, noop_sink
from world.crime.models import CLOSED_CASE_STATUSES, ArrestRecord, WarrantStatus
from world.crime.ports import GovernmentPort, LocationPort, require_co_located
from world.crime.store import CrimeStore


class ArrestDesk:
    def __init__(
        self,
        store: CrimeStore,
        government: GovernmentPort,
        location: LocationPort,
        event_sink: EventSink | None = None,
    ) -> None:
        self.store = store
        self.government = government
        self.location = location
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def arrest(
        self,
        case_id: str,
        officer_agent_id: str,
        target_agent_id: str,
        now_tick: int,
        warrant_id: str | None = None,
    ) -> ArrestRecord:
        if not await self.government.is_police_officer(officer_agent_id):
            raise NotPoliceOfficer(officer_agent_id)

        case = await self.store.get_case(case_id)
        if case is None:
            raise CaseNotFound(case_id)
        if case.status in CLOSED_CASE_STATUSES:
            raise CaseAlreadyClosed(case_id, case.status.value)

        if warrant_id is not None:
            warrant = await self.store.get_warrant(warrant_id)
            if warrant is None:
                raise WarrantNotFound(warrant_id)
            if warrant.case_id != case_id or warrant.target_agent_id != target_agent_id:
                raise WarrantNotGranted(warrant_id, "does not name this case and target")
            if warrant.status is not WarrantStatus.GRANTED:
                raise WarrantNotGranted(warrant_id, f"status is {warrant.status.value!r}, not granted")

        location_id = require_co_located(self.location, officer_agent_id, target_agent_id)

        arrest = ArrestRecord(
            case_id=case_id,
            officer_agent_id=officer_agent_id,
            target_agent_id=target_agent_id,
            tick=now_tick,
            location_id=location_id,
            warrant_id=warrant_id,
        )
        await self.store.append_arrest(arrest)
        await self.event_sink(
            build_event(
                EVENT_ARREST_MADE,
                {
                    "arrest_id": arrest.arrest_id,
                    "case_id": case_id,
                    "location_id": location_id,
                    "target_agent_id": target_agent_id,
                    "involved_agent_ids": [officer_agent_id, target_agent_id],
                    "warrant_id": warrant_id,
                },
            )
        )
        return arrest
