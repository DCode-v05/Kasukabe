"""Land petitions to the President (DESIGN.md section 1: "Land expansion:
government petitions the President (the operator). On approval the app
appends tiles to the Tiled JSON, regenerates collision, and the town
physically grows").

Filing is a government action (only a sitting seat holder petitions the
President - DESIGN.md: "government petitions the President", not any
agent) gated at the government complex like the rest of `government.py`'s
actions. The decision belongs to the President alone (DESIGN.md section 8:
"President: land petitions (approve -> map grows)") - no location gate on
`approve_petition`/`deny_petition` since the President is the operator
console, not an in-world agent with a position on the tile grid.

GOV-11 reads the absence of an in-world `approve_petition` TOOL as a
gap. It is the design: the President is the operator, so approval is an
operator act and giving a resident a tool for it would hand the town a
power DESIGN.md deliberately keeps outside it. What GOV-11 is right
about is the second half - "on the production host, decision-only" -
and that is closed where the map service is wired (see below and
`run_genesis.build_the_map`).

`list_pending` is the read this module offers the operator console
(DESIGN.md: "Land petition to President flows to the operator console").
The flow itself is the `politics.petition.filed` event this module emits
plus this read method; `game/`'s console (out of this module's scope,
per app/CONVENTIONS.md's module boundaries) is what actually renders the
queue and calls `approve_petition`/`deny_petition` over world's HTTP API.

Approving a petition here does NOT grow the map, and the module that
does is no longer hypothetical.

GROW-30. This used to describe the Tiled-JSON mutation as the job of a
separate land/map module that did not exist yet, triggered by the
`politics.petition.approved` event this module emits. Both halves are
now wrong. `world/map/service.py` is built and `MapService.expand_land`
does the work; `admin_api.py:684` calls it DIRECTLY when the operator
approves.

The approval event IS consumed - `wiring.py`'s `record_petition_if_new`
writes it to the `petitions` trace table (GROW-19/INFRA-17) - but by a
RECORDER, not by anything that grows the map. Nothing subscribes in
order to expand.

So the approval event is a record of a decision, not a trigger. If a
subscriber is ever wanted, it has to be written; until then, do not
read the emit as though something were listening.
"""

from __future__ import annotations

from world.charter.errors import (
    NotAtRequiredLocation,
    NotSittingGovernment,
    PetitionNotFound,
    PetitionNotPending,
)
from world.charter.events import (
    EVENT_PETITION_APPROVED,
    EVENT_PETITION_DENIED,
    EVENT_PETITION_FILED,
    EventSink,
    build_event,
    noop_sink,
)
from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, LandPetition, PetitionStatus
from world.charter.store import CharterStore


class LandPetitionQueue:
    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def file_petition(
        self,
        filed_by_agent_id: str,
        title: str,
        description: str,
        tile_count_requested: int,
        location: str,
        now_tick: int,
    ) -> LandPetition:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)
        government_state = await self.store.get_government_state()
        if not government_state.is_sitting(filed_by_agent_id):
            raise NotSittingGovernment(filed_by_agent_id)

        petition = LandPetition(
            filed_by_agent_id=filed_by_agent_id,
            title=title,
            description=description,
            tile_count_requested=tile_count_requested,
            filed_tick=now_tick,
        )
        await self.store.put_land_petition(petition)
        await self.event_sink(
            build_event(
                EVENT_PETITION_FILED,
                {
                    "petition_id": petition.petition_id,
                    "filed_by_agent_id": filed_by_agent_id,
                    "title": title,
                    "tile_count_requested": tile_count_requested,
                },
            )
        )
        return petition

    async def list_pending(self) -> list[LandPetition]:
        petitions = await self.store.list_land_petitions()
        return [p for p in petitions if p.status == PetitionStatus.PENDING]

    async def load_petition(self, petition_id: str) -> LandPetition:
        petition = await self.store.get_land_petition(petition_id)
        if petition is None:
            raise PetitionNotFound(petition_id)
        return petition

    async def approve_petition(
        self, petition_id: str, decision_note: str, now_tick: int
    ) -> LandPetition:
        petition = await self.load_petition(petition_id)
        if petition.status != PetitionStatus.PENDING:
            raise PetitionNotPending(petition_id, petition.status.value)
        petition.status = PetitionStatus.APPROVED
        petition.decided_tick = now_tick
        petition.decision_note = decision_note
        await self.store.put_land_petition(petition)
        await self.event_sink(
            build_event(
                EVENT_PETITION_APPROVED,
                {
                    "petition_id": petition_id,
                    "tile_count_requested": petition.tile_count_requested,
                    "decision_note": decision_note,
                },
            )
        )
        return petition

    async def deny_petition(
        self, petition_id: str, decision_note: str, now_tick: int
    ) -> LandPetition:
        petition = await self.load_petition(petition_id)
        if petition.status != PetitionStatus.PENDING:
            raise PetitionNotPending(petition_id, petition.status.value)
        petition.status = PetitionStatus.DENIED
        petition.decided_tick = now_tick
        petition.decision_note = decision_note
        await self.store.put_land_petition(petition)
        await self.event_sink(
            build_event(
                EVENT_PETITION_DENIED, {"petition_id": petition_id, "decision_note": decision_note}
            )
        )
        return petition
