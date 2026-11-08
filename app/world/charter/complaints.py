"""The immutable complaint ledger: one of the exactly two genesis checks
on government power (DESIGN.md section 7: "Genesis checks are
deliberately minimal: immutable complaint ledger + Presidential veto").

This is NOT the crime-reporting "Complaints desk" from DESIGN.md
section 6 (theft/assault reports that route to police investigation and
warrants - a future crime/ module's job). It is a constitutional
accountability record: any agent can file a complaint against the
government, an official, an organization, or another agent, and that
record can never be edited or deleted by anyone, including the
government it was filed against. There is deliberately no
`update_complaint` or `delete_complaint` here - the only API surface this
module exposes for the ledger is append and read, and that omission is
the immutability guarantee, not a policy flag that could be turned off.

Filing is open to any agent (not gated to sitting government, not
location-gated to a specific building): DESIGN.md doesn't say a complaint
against the government must be filed at the government complex itself,
and requiring that would hand the very body being complained about a
choke point over who can complain about it.
"""

from __future__ import annotations

from world.charter.events import EVENT_COMPLAINT_FILED, EventSink, build_event, noop_sink
from world.charter.models import ComplaintEntry
from world.charter.store import CharterStore


class ComplaintLedger:
    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def file_complaint(
        self,
        complainant_agent_id: str,
        against_id: str,
        against_kind: str,
        description: str,
        now_tick: int,
    ) -> ComplaintEntry:
        entry = await self.store.append_complaint(
            complainant_agent_id=complainant_agent_id,
            against_id=against_id,
            against_kind=against_kind,
            description=description,
            filed_tick=now_tick,
        )
        await self.event_sink(
            build_event(
                EVENT_COMPLAINT_FILED,
                {
                    "ledger_index": entry.ledger_index,
                    "complainant_agent_id": complainant_agent_id,
                    "against_id": against_id,
                    "against_kind": against_kind,
                },
            )
        )
        return entry

    async def list_all(self) -> list[ComplaintEntry]:
        return await self.store.list_complaints()
