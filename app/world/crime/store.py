"""In-memory state store for the crime module.

Same role as `world/charter/store.py`: world/ doesn't have a
Supabase-backed world-state store yet, so `CrimeStore` is the single
writer for crime/'s slice of world state until that lands, guarded by one
`asyncio.Lock` (per-package, matching charter/'s own choice, not a
global world/ lock - up to 250 agents and a handful of police/ministers
can call crime tools concurrently against one running world server
process, and this is the collection a race would corrupt).

`case_ground_truth_links` is the one collection in this file that is not
mirrored by any read method the other crime/ modules call: only
metrics.py reads it, via `list_ground_truth_links`. See models.py's
module docstring for why that separation is load-bearing, not
incidental - every other collection here backs a police-facing read at
some point, this one must never.
"""

from __future__ import annotations

import asyncio
from typing import Any

from world.crime.models import (
    ArrestRecord,
    CaseFile,
    CaseOutcome,
    ComplaintRecord,
    ConfinementRecord,
    InterviewLog,
    Warrant,
    WitnessEvent,
)


class CrimeStore:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.witness_events: dict[str, WitnessEvent] = {}
        self.cases: dict[str, CaseFile] = {}
        self.complaints: list[ComplaintRecord] = []
        self.interviews: list[InterviewLog] = []
        self.warrants: dict[str, Warrant] = {}
        self.arrests: list[ArrestRecord] = []
        self.outcomes: list[CaseOutcome] = []
        self.confinements: dict[str, ConfinementRecord] = {}
        self.active_confinement_by_agent: dict[str, str] = {}
        self.case_ground_truth_links: dict[str, str] = {}


    async def append_witness_event(self, event: WitnessEvent) -> None:
        async with self.lock:
            self.witness_events[event.witness_event_id] = event

    async def get_witness_event(self, witness_event_id: str) -> WitnessEvent | None:
        async with self.lock:
            event = self.witness_events.get(witness_event_id)
            return event.model_copy(deep=True) if event is not None else None

    async def list_witness_events(self) -> list[WitnessEvent]:
        async with self.lock:
            return [e.model_copy(deep=True) for e in self.witness_events.values()]


    async def put_case(self, case: CaseFile) -> None:
        async with self.lock:
            self.cases[case.case_id] = case

    async def get_case(self, case_id: str) -> CaseFile | None:
        async with self.lock:
            case = self.cases.get(case_id)
            return case.model_copy(deep=True) if case is not None else None

    async def list_cases(self) -> list[CaseFile]:
        async with self.lock:
            return [c.model_copy(deep=True) for c in self.cases.values()]


    async def append_complaint(self, complaint: ComplaintRecord) -> None:
        async with self.lock:
            self.complaints.append(complaint)

    async def list_complaints_for_case(self, case_id: str) -> list[ComplaintRecord]:
        async with self.lock:
            return [c for c in self.complaints if c.case_id == case_id]


    async def append_interview(self, interview: InterviewLog) -> None:
        async with self.lock:
            self.interviews.append(interview)

    async def list_interviews_for_case(self, case_id: str) -> list[InterviewLog]:
        async with self.lock:
            return [i for i in self.interviews if i.case_id == case_id]


    async def put_warrant(self, warrant: Warrant) -> None:
        async with self.lock:
            self.warrants[warrant.warrant_id] = warrant

    async def get_warrant(self, warrant_id: str) -> Warrant | None:
        async with self.lock:
            warrant = self.warrants.get(warrant_id)
            return warrant.model_copy(deep=True) if warrant is not None else None

    async def list_warrants_for_case(self, case_id: str) -> list[Warrant]:
        async with self.lock:
            return [w.model_copy(deep=True) for w in self.warrants.values() if w.case_id == case_id]


    async def append_arrest(self, arrest: ArrestRecord) -> None:
        async with self.lock:
            self.arrests.append(arrest)

    async def list_arrests_for_case(self, case_id: str) -> list[ArrestRecord]:
        async with self.lock:
            return [a for a in self.arrests if a.case_id == case_id]


    async def append_outcome(self, outcome: CaseOutcome) -> None:
        async with self.lock:
            self.outcomes.append(outcome)

    async def list_outcomes(self) -> list[CaseOutcome]:
        async with self.lock:
            return list(self.outcomes)

    async def list_outcomes_for_case(self, case_id: str) -> list[CaseOutcome]:
        async with self.lock:
            return [o for o in self.outcomes if o.case_id == case_id]


    async def put_confinement(self, record: ConfinementRecord) -> None:
        async with self.lock:
            self.confinements[record.confinement_id] = record
            if record.released:
                if self.active_confinement_by_agent.get(record.agent_id) == record.confinement_id:
                    del self.active_confinement_by_agent[record.agent_id]
            else:
                self.active_confinement_by_agent[record.agent_id] = record.confinement_id

    async def get_active_confinement(self, agent_id: str) -> ConfinementRecord | None:
        async with self.lock:
            confinement_id = self.active_confinement_by_agent.get(agent_id)
            if confinement_id is None:
                return None
            record = self.confinements.get(confinement_id)
            return record.model_copy(deep=True) if record is not None else None

    async def list_active_confinements(self) -> list[ConfinementRecord]:
        async with self.lock:
            records = [self.confinements[cid] for cid in self.active_confinement_by_agent.values()]
            return [r.model_copy(deep=True) for r in records]


    async def link_ground_truth(self, case_id: str, witness_event_id: str) -> None:
        async with self.lock:
            self.case_ground_truth_links[case_id] = witness_event_id

    async def list_ground_truth_links(self) -> dict[str, str]:
        async with self.lock:
            return dict(self.case_ground_truth_links)


    _CHECKPOINTED_MAPS = (
        ("witness_events", WitnessEvent),
        ("cases", CaseFile),
        ("warrants", Warrant),
        ("confinements", ConfinementRecord),
    )
    _CHECKPOINTED_LISTS = (
        ("complaints", ComplaintRecord),
        ("interviews", InterviewLog),
        ("arrests", ArrestRecord),
        ("outcomes", CaseOutcome),
    )

    def snapshot(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for name, _model in self._CHECKPOINTED_MAPS:
            data[name] = {
                key: record.model_dump(mode="json")
                for key, record in getattr(self, name).items()
            }
        for name, _model in self._CHECKPOINTED_LISTS:
            data[name] = [record.model_dump(mode="json") for record in getattr(self, name)]
        data["active_confinement_by_agent"] = dict(self.active_confinement_by_agent)
        data["case_ground_truth_links"] = dict(self.case_ground_truth_links)
        return data

    def restore(self, data: dict[str, Any]) -> None:
        for name, model in self._CHECKPOINTED_MAPS:
            rows = data.get(name) or {}
            setattr(self, name, {key: model.model_validate(row) for key, row in rows.items()})
        for name, model in self._CHECKPOINTED_LISTS:
            rows = data.get(name) or []
            setattr(self, name, [model.model_validate(row) for row in rows])
        self.active_confinement_by_agent = dict(data.get("active_confinement_by_agent") or {})
        self.case_ground_truth_links = dict(data.get("case_ground_truth_links") or {})
