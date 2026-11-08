"""Organizations: parties, cults, religions, societies (DESIGN.md
section 7: "Organizations first-class: parties, cults, societies,
religions - registered charters or underground").

Registration is open to any agent (not gated to sitting government -
unlike `government.py`'s actions, forming a party or a cult is a citizen
act, not a state one), gated at the government complex since that is
where the "register" primitive happens (DESIGN.md section 2 primitives:
"register (business/org)").

Underground organizations are not represented here at all: DESIGN.md is
explicit that unregistered coordination is "detectable in the graph and
is itself data" - meaning a future relationship-graph analysis over
`agent`/`utterance`/`relationship` tables finds it, not a row in this
module's `Organization` store. An `Organization` record here always means
"registered."
"""

from __future__ import annotations

from world.charter.errors import (
    DuplicateOrganizationName,
    NotAtRequiredLocation,
    NotOrganizationMember,
    OrganizationNotFound,
)
from world.charter.events import (
    EVENT_ORGANIZATION_MEMBER_ADDED,
    EVENT_ORGANIZATION_MEMBER_REMOVED,
    EVENT_ORGANIZATION_REGISTERED,
    EventSink,
    build_event,
    noop_sink,
)
from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, Organization, OrganizationKind
from world.charter.store import CharterStore


class OrganizationRegistry:
    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def register_organization(
        self,
        kind: OrganizationKind,
        name: str,
        founder_agent_id: str,
        charter_text: str,
        location: str,
        now_tick: int,
    ) -> Organization:
        if location != LOCATION_GOVERNMENT_COMPLEX:
            raise NotAtRequiredLocation(LOCATION_GOVERNMENT_COMPLEX, location)

        existing = await self.store.list_organizations()
        if any(o.name == name for o in existing):
            raise DuplicateOrganizationName(name)

        organization = Organization(
            kind=kind,
            name=name,
            founder_agent_id=founder_agent_id,
            charter_text=charter_text,
            founded_tick=now_tick,
            members={founder_agent_id: "founder"},
        )
        await self.store.put_organization(organization)
        await self.event_sink(
            build_event(
                EVENT_ORGANIZATION_REGISTERED,
                {
                    "org_id": organization.org_id,
                    "kind": kind.value,
                    "name": name,
                    "founder_agent_id": founder_agent_id,
                },
            )
        )
        return organization

    async def load_organization(self, org_id: str) -> Organization:
        organization = await self.store.get_organization(org_id)
        if organization is None:
            raise OrganizationNotFound(org_id)
        return organization

    async def add_member(self, org_id: str, agent_id: str, now_tick: int) -> Organization:
        organization = await self.load_organization(org_id)
        organization.members.setdefault(agent_id, "member")
        await self.store.put_organization(organization)
        await self.event_sink(
            build_event(
                EVENT_ORGANIZATION_MEMBER_ADDED,
                {"org_id": org_id, "agent_id": agent_id, "tick": now_tick},
            )
        )
        return organization

    async def remove_member(self, org_id: str, agent_id: str, now_tick: int) -> Organization:
        organization = await self.load_organization(org_id)
        if agent_id not in organization.members:
            raise NotOrganizationMember(org_id, agent_id)
        del organization.members[agent_id]
        await self.store.put_organization(organization)
        await self.event_sink(
            build_event(
                EVENT_ORGANIZATION_MEMBER_REMOVED,
                {"org_id": org_id, "agent_id": agent_id, "tick": now_tick},
            )
        )
        return organization

    async def list_by_kind(self, kind: OrganizationKind) -> list[Organization]:
        organizations = await self.store.list_organizations()
        return [o for o in organizations if o.kind == kind]
