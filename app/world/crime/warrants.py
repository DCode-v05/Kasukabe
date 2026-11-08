"""Warrant requests (DESIGN.md section 6: "minister-signed warrant ->
fine or jail").

A warrant is the one judicial-authorization gate in this whole package:
outcomes.py refuses to move a single sim unit or open a cell without a
GRANTED warrant naming the convicted agent for the same case. Requesting
one is a police action (an officer builds the request from whatever
investigation.py surfaced to them); granting or denying one is a
minister-only action, checked against `GovernmentPort.is_minister`, never
against `is_police_officer` - DESIGN.md says "minister-signed", not
"government-signed", and this module takes that word choice literally
rather than also letting the Prime Minister sign (see ports.py's
`GovernmentPort` docstring for the same distinction charter/ already
draws between `pm_agent_id` and `minister_agent_ids`).

Both requesting and deciding are gated to the government complex, the
same bureaucratic seat charter/'s patent office and license issuance use
(`world/charter/patent_office.py`, `world/charter/government.py`) - a
warrant is paperwork before it is anything else.
"""

from __future__ import annotations

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.crime.errors import (
    CaseAlreadyClosed,
    CaseNotFound,
    NotAtRequiredLocation,
    NotMinister,
    NotPoliceOfficer,
    WarrantNotFound,
    WarrantNotPending,
)
from world.crime.events import (
    EVENT_WARRANT_DENIED,
    EVENT_WARRANT_ISSUED,
    EVENT_WARRANT_REQUESTED,
    EventSink,
    build_event,
    noop_sink,
)
from world.crime.models import CLOSED_CASE_STATUSES, Warrant, WarrantStatus
from world.crime.ports import GovernmentPort
from world.crime.store import CrimeStore


class WarrantOffice:
    def __init__(self, store: CrimeStore, government: GovernmentPort, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.government = government
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def require_location(self, location: str) -> None:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)

    async def request_warrant(
        self,
        case_id: str,
        officer_agent_id: str,
        target_agent_id: str,
        justification: str,
        location: str,
        now_tick: int,
    ) -> Warrant:
        if not await self.government.is_police_officer(officer_agent_id):
            raise NotPoliceOfficer(officer_agent_id)
        await self.require_location(location)

        case = await self.store.get_case(case_id)
        if case is None:
            raise CaseNotFound(case_id)
        if case.status in CLOSED_CASE_STATUSES:
            raise CaseAlreadyClosed(case_id, case.status.value)

        warrant = Warrant(
            case_id=case_id,
            requested_by_officer_agent_id=officer_agent_id,
            target_agent_id=target_agent_id,
            justification=justification,
            requested_tick=now_tick,
        )
        await self.store.put_warrant(warrant)
        await self.event_sink(
            build_event(
                EVENT_WARRANT_REQUESTED,
                {
                    "warrant_id": warrant.warrant_id,
                    "case_id": case_id,
                    "location_id": location,
                    "target_agent_id": target_agent_id,
                    "involved_agent_ids": [officer_agent_id, target_agent_id],
                },
            )
        )
        return warrant

    async def require_pending_warrant(self, warrant_id: str) -> Warrant:
        warrant = await self.store.get_warrant(warrant_id)
        if warrant is None:
            raise WarrantNotFound(warrant_id)
        if warrant.status is not WarrantStatus.REQUESTED:
            raise WarrantNotPending(warrant_id, warrant.status.value)
        return warrant

    async def sign_warrant(self, warrant_id: str, minister_agent_id: str, location: str, now_tick: int) -> Warrant:
        if not await self.government.is_minister(minister_agent_id):
            raise NotMinister(minister_agent_id)
        await self.require_location(location)

        warrant = await self.require_pending_warrant(warrant_id)
        warrant.status = WarrantStatus.GRANTED
        warrant.decided_by_minister_agent_id = minister_agent_id
        warrant.decided_tick = now_tick
        await self.store.put_warrant(warrant)

        await self.event_sink(
            build_event(
                EVENT_WARRANT_ISSUED,
                {
                    "warrant_id": warrant.warrant_id,
                    "case_id": warrant.case_id,
                    "location_id": location,
                    "target_agent_id": warrant.target_agent_id,
                    "involved_agent_ids": [minister_agent_id, warrant.target_agent_id],
                },
            )
        )
        return warrant

    async def deny_warrant(
        self, warrant_id: str, minister_agent_id: str, reason: str, location: str, now_tick: int
    ) -> Warrant:
        if not await self.government.is_minister(minister_agent_id):
            raise NotMinister(minister_agent_id)
        await self.require_location(location)

        warrant = await self.require_pending_warrant(warrant_id)
        warrant.status = WarrantStatus.DENIED
        warrant.decided_by_minister_agent_id = minister_agent_id
        warrant.decided_tick = now_tick
        warrant.denial_reason = reason
        await self.store.put_warrant(warrant)

        await self.event_sink(
            build_event(
                EVENT_WARRANT_DENIED,
                {
                    "warrant_id": warrant.warrant_id,
                    "case_id": warrant.case_id,
                    "location_id": location,
                    "target_agent_id": warrant.target_agent_id,
                    "involved_agent_ids": [minister_agent_id, warrant.target_agent_id],
                    "reason": reason,
                },
            )
        )
        return warrant

    async def list_warrants(self, case_id: str) -> list[Warrant]:
        return await self.store.list_warrants_for_case(case_id)
