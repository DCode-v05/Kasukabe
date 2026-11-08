"""Patent office: review queue for agent-invented tools (DESIGN.md
section 2: invented tools "must be registered at the government patent
office (fee; reviewable; revocable)").

`charter/` doesn't have a tool registry to hand a newly-approved patent
the actual composed-tool implementation - that lives in a different
module, `world/tools/meta.py` (DESIGN.md section 2's "meta-tool" system,
`toolify`/`skillify`). GROW-30: this used to call that module unbuilt. This
module owns the bureaucratic side only: filing, fee validation against
the charter's `patent_filing_fee_sim`, government review (approve/reject),
and revocation. The actual money movement for the fee is `world/`'s
ledger's job once it exists (app/CONVENTIONS.md: only world/'s ledger and
broker/ ever mutate a wallet); this module validates that the amount the
caller says was paid meets the charter's fee, it does not debit anyone.
"""

from __future__ import annotations

from world.charter.errors import (
    InsufficientFee,
    NotAtRequiredLocation,
    NotSittingGovernment,
    PatentAlreadyRevoked,
    PatentApplicationNotFound,
    PatentApplicationNotPending,
    PatentNotFound,
)
from world.charter.events import (
    EVENT_PATENT_APPROVED,
    EVENT_PATENT_FILED,
    EVENT_PATENT_REJECTED,
    EVENT_PATENT_REVOKED,
    EventSink,
    build_event,
    noop_sink,
)
from world.charter.models import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_PATENT_OFFICE,
    Patent,
    PatentApplication,
    PatentStatus,
)
from world.charter.store import CharterStore


class PatentOffice:
    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def file_application(
        self,
        inventor_agent_id: str,
        tool_name: str,
        description: str,
        composed_of: list[str],
        fee_paid_sim: int,
        location: str,
        now_tick: int,
    ) -> PatentApplication:
        if location != LOCATION_PATENT_OFFICE:
            raise NotAtRequiredLocation(LOCATION_PATENT_OFFICE, location)
        charter = await self.store.get_charter()
        if fee_paid_sim < charter.patent_filing_fee_sim:
            raise InsufficientFee(charter.patent_filing_fee_sim, fee_paid_sim)

        application = PatentApplication(
            inventor_agent_id=inventor_agent_id,
            tool_name=tool_name,
            description=description,
            composed_of=list(composed_of),
            fee_paid_sim=fee_paid_sim,
            filed_tick=now_tick,
        )
        await self.store.put_patent_application(application)
        await self.event_sink(
            build_event(
                EVENT_PATENT_FILED,
                {
                    "application_id": application.application_id,
                    "inventor_agent_id": inventor_agent_id,
                    "tool_name": tool_name,
                },
            )
        )
        return application

    async def require_reviewer(self, reviewer_agent_id: str, location: str) -> None:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)
        government_state = await self.store.get_government_state()
        if not government_state.is_sitting(reviewer_agent_id):
            raise NotSittingGovernment(reviewer_agent_id)

    async def approve_application(
        self, application_id: str, reviewer_agent_id: str, location: str, now_tick: int
    ) -> Patent:
        await self.require_reviewer(reviewer_agent_id, location)
        application = await self.store.get_patent_application(application_id)
        if application is None:
            raise PatentApplicationNotFound(application_id)
        if application.status != PatentStatus.PENDING:
            raise PatentApplicationNotPending(application_id, application.status.value)

        application.status = PatentStatus.APPROVED
        application.reviewed_by_agent_id = reviewer_agent_id
        application.reviewed_tick = now_tick
        await self.store.put_patent_application(application)

        patent = Patent(
            application_id=application_id,
            tool_name=application.tool_name,
            owner_agent_id=application.inventor_agent_id,
            granted_by_agent_id=reviewer_agent_id,
            granted_tick=now_tick,
        )
        await self.store.put_patent(patent)
        await self.event_sink(
            build_event(
                EVENT_PATENT_APPROVED,
                {
                    "application_id": application_id,
                    "patent_id": patent.patent_id,
                    "tool_name": patent.tool_name,
                },
            )
        )
        return patent

    async def reject_application(
        self, application_id: str, reviewer_agent_id: str, reason: str, location: str, now_tick: int
    ) -> PatentApplication:
        await self.require_reviewer(reviewer_agent_id, location)
        application = await self.store.get_patent_application(application_id)
        if application is None:
            raise PatentApplicationNotFound(application_id)
        if application.status != PatentStatus.PENDING:
            raise PatentApplicationNotPending(application_id, application.status.value)

        application.status = PatentStatus.REJECTED
        application.reviewed_by_agent_id = reviewer_agent_id
        application.reviewed_tick = now_tick
        application.reject_reason = reason
        await self.store.put_patent_application(application)
        await self.event_sink(
            build_event(EVENT_PATENT_REJECTED, {"application_id": application_id, "reason": reason})
        )
        return application

    async def revoke_patent(
        self, patent_id: str, revoked_by_agent_id: str, reason: str, location: str, now_tick: int
    ) -> Patent:
        await self.require_reviewer(revoked_by_agent_id, location)
        patent = await self.store.get_patent(patent_id)
        if patent is None:
            raise PatentNotFound(patent_id)
        if patent.revoked:
            raise PatentAlreadyRevoked(patent_id)

        patent.revoked = True
        patent.revoked_by_agent_id = revoked_by_agent_id
        patent.revoked_tick = now_tick
        patent.revoke_reason = reason
        await self.store.put_patent(patent)
        await self.event_sink(
            build_event(EVENT_PATENT_REVOKED, {"patent_id": patent_id, "reason": reason})
        )
        return patent
