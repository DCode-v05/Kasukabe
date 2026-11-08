"""The crime complaints desk (DESIGN.md section 6: "Complaints desk at
government -> case file").

Not to be confused with `world.charter.complaints.ComplaintLedger`: that
module's docstring already flags the distinction from the government side
("This is NOT the crime-reporting Complaints desk... a future crime/
module's job") - this is that module. Complaints filed here are immutable
once recorded (no update/delete method exists, matching the constitutional
ledger's own immutability-by-omission), but the `CaseFile` a complaint
opens or attaches to is a mutable status tracker over that immutable
trail, the same shape `world.charter.models.Job` is over the
constitutional ledger.

Gated to the government complex, reusing charter/'s own constant rather
than a duplicate literal string - see this module's import of
`LOCATION_GOVERNMENT_COMPLEX`. This is the one place in crime/ that
imports from charter/ directly: charter has no reverse dependency on
crime, so it is a safe one-directional reuse of an existing constant, not
a coupling that could cycle.
"""

from __future__ import annotations

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.crime.errors import CaseAlreadyClosed, CaseNotFound, NotAtRequiredLocation
from world.crime.events import (
    EVENT_CASE_OPENED,
    EVENT_COMPLAINT_FILED,
    EventSink,
    build_event,
    noop_sink,
)
from world.crime.models import CLOSED_CASE_STATUSES, CaseFile, ComplaintRecord, CrimeKind
from world.crime.store import CrimeStore


class ComplaintDesk:
    def __init__(self, store: CrimeStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def require_location(self, location: str) -> None:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)

    async def file_complaint(
        self,
        complainant_agent_id: str,
        kind: CrimeKind,
        description: str,
        location: str,
        now_tick: int,
        accused_agent_id: str | None = None,
        witness_event_id: str | None = None,
        case_id: str | None = None,
    ) -> tuple[ComplaintRecord, CaseFile]:
        """File one complaint. `case_id=None` opens a fresh case;
        supplying an existing open case's id instead attaches a second
        (or third...) complaint to it - DESIGN.md doesn't say a case is
        limited to one complainant, and multiple witnesses corroborating
        the same case is exactly the kind of evidence an investigation
        should be able to accumulate.

        `witness_event_id` is for internal bookkeeping only (the future
        tool dispatcher may know which ground-truth incident a complaint
        refers to, e.g. because the victim just lived through it) - it is
        never returned to the caller and never attached to the `CaseFile`
        itself, see models.py's module docstring for why that separation
        is load-bearing.
        """
        await self.require_location(location)

        if case_id is None:
            case = CaseFile(
                kind=kind,
                opened_tick=now_tick,
                opened_by_agent_id=complainant_agent_id,
                accused_agent_id=accused_agent_id,
            )
            await self.store.put_case(case)
            await self.event_sink(
                build_event(
                    EVENT_CASE_OPENED,
                    {
                        "case_id": case.case_id,
                        "kind": kind.value,
                        "location_id": location,
                        "involved_agent_ids": [complainant_agent_id]
                        + ([accused_agent_id] if accused_agent_id else []),
                    },
                )
            )
        else:
            existing = await self.store.get_case(case_id)
            if existing is None:
                raise CaseNotFound(case_id)
            if existing.status in CLOSED_CASE_STATUSES:
                raise CaseAlreadyClosed(case_id, existing.status.value)
            case = existing
            if accused_agent_id is not None and case.accused_agent_id is None:
                case.accused_agent_id = accused_agent_id
                await self.store.put_case(case)

        complaint = ComplaintRecord(
            case_id=case.case_id,
            complainant_agent_id=complainant_agent_id,
            accused_agent_id=accused_agent_id,
            kind=kind,
            description=description,
            filed_tick=now_tick,
        )
        await self.store.append_complaint(complaint)

        if witness_event_id is not None:
            await self.store.link_ground_truth(case.case_id, witness_event_id)

        await self.event_sink(
            build_event(
                EVENT_COMPLAINT_FILED,
                {
                    "complaint_id": complaint.complaint_id,
                    "case_id": case.case_id,
                    "location_id": location,
                    "involved_agent_ids": [complainant_agent_id]
                    + ([accused_agent_id] if accused_agent_id else []),
                },
            )
        )
        return complaint, case

    async def get_case(self, case_id: str) -> CaseFile:
        case = await self.store.get_case(case_id)
        if case is None:
            raise CaseNotFound(case_id)
        return case

    async def list_cases(self) -> list[CaseFile]:
        return await self.store.list_cases()

    async def list_complaints(self, case_id: str) -> list[ComplaintRecord]:
        case = await self.store.get_case(case_id)
        if case is None:
            raise CaseNotFound(case_id)
        return await self.store.list_complaints_for_case(case_id)
