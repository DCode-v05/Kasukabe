"""Police investigation: DESIGN.md section 6, "police... investigate:
interviews, public records (NOT omniscient)".

This module is the epistemic boundary of the whole justice system. It
never imports `world.crime.models.WitnessEvent` (ground truth) and never
will - every method here builds its answer purely from `ComplaintRecord`
and `InterviewLog` rows, what agents have actually said on the record.

The "NOT omniscient" half is enforced structurally by
`read_case_evidence`, not by a permission flag on the data: a requesting
officer always sees every complaint filed on a case (they were filed at
an open desk, DESIGN.md's "public records" half) plus every PUBLIC
interview any officer logged, plus their OWN PRIVATE interviews - and
nothing else. There is no argument that widens this, no admin override;
an officer who wants a colleague's private notes has to ask the colleague
to mark them public, or re-interview the same witness themselves.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.crime.errors import CaseAlreadyClosed, CaseNotFound, NotPoliceOfficer
from world.crime.events import EVENT_INTERVIEW_LOGGED, EventSink, build_event, noop_sink
from world.crime.models import (
    CLOSED_CASE_STATUSES,
    CaseFile,
    CaseStatus,
    ComplaintRecord,
    InterviewLog,
    InterviewVisibility,
)
from world.crime.ports import GovernmentPort, LocationPort, require_co_located
from world.crime.store import CrimeStore


class CaseEvidenceView(BaseModel):
    """What one officer's "read my case" tool call returns: the case
    itself (ground-truth-free by construction, see models.py), every
    complaint on file, and only the interviews this officer is entitled
    to see. Two officers asking about the same case at the same moment
    can get two different `visible_interviews` lists - that asymmetry is
    the point, not a bug.
    """

    case: CaseFile
    complaints: list[ComplaintRecord] = Field(default_factory=list)
    visible_interviews: list[InterviewLog] = Field(default_factory=list)


class InvestigationBoard:
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

    async def log_interview(
        self,
        case_id: str,
        officer_agent_id: str,
        interviewee_agent_id: str,
        summary: str,
        now_tick: int,
        visibility: InterviewVisibility = InterviewVisibility.PUBLIC,
    ) -> InterviewLog:
        """Log one interview. Co-location is the location gate here
        (DESIGN.md section 2: "every tool is location-gated") rather than
        a fixed building - an interview happens wherever the interviewee
        actually is, in person, unlike the bureaucratic desk.py /
        warrants.py actions that are anchored to the government complex.
        """
        await self.require_officer(officer_agent_id)
        case = await self.require_open_case(case_id)
        location_id = require_co_located(self.location, officer_agent_id, interviewee_agent_id)

        if case.status is CaseStatus.OPEN:
            case.status = CaseStatus.UNDER_INVESTIGATION
            await self.store.put_case(case)

        interview = InterviewLog(
            case_id=case_id,
            officer_agent_id=officer_agent_id,
            interviewee_agent_id=interviewee_agent_id,
            tick=now_tick,
            location_id=location_id,
            summary=summary,
            visibility=visibility,
        )
        await self.store.append_interview(interview)
        await self.event_sink(
            build_event(
                EVENT_INTERVIEW_LOGGED,
                {
                    "interview_id": interview.interview_id,
                    "case_id": case_id,
                    "location_id": location_id,
                    "involved_agent_ids": [officer_agent_id, interviewee_agent_id],
                    "visibility": visibility.value,
                },
            )
        )
        return interview

    async def read_case_evidence(self, case_id: str, requesting_officer_agent_id: str) -> CaseEvidenceView:
        await self.require_officer(requesting_officer_agent_id)
        case = await self.store.get_case(case_id)
        if case is None:
            raise CaseNotFound(case_id)

        complaints = await self.store.list_complaints_for_case(case_id)
        all_interviews = await self.store.list_interviews_for_case(case_id)
        visible_interviews = [
            interview
            for interview in all_interviews
            if interview.visibility is InterviewVisibility.PUBLIC
            or interview.officer_agent_id == requesting_officer_agent_id
        ]
        return CaseEvidenceView(case=case, complaints=complaints, visible_interviews=visible_interviews)
