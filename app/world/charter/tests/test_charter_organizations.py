"""Tests for world/charter/organizations.py: register parties, cults,
religions, societies with member rosters.
"""

from __future__ import annotations

import pytest

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
)
from world.charter.genesis import seed_genesis
from world.charter.models import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL, OrganizationKind
from world.charter.organizations import OrganizationRegistry
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder


async def make_registry(
    recorder: EventRecorder | None = None,
) -> tuple[OrganizationRegistry, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    return OrganizationRegistry(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_register_requires_government_complex() -> None:
    registry, _ = await make_registry()

    with pytest.raises(NotAtRequiredLocation):
        await registry.register_organization(
            OrganizationKind.CULT,
            "The Circle",
            "founder-1",
            "we believe",
            LOCATION_TOWN_HALL,
            now_tick=0,
        )


@pytest.mark.asyncio
async def test_register_organization_makes_founder_a_member() -> None:
    recorder = EventRecorder()
    registry, _ = await make_registry(recorder)

    org = await registry.register_organization(
        OrganizationKind.PARTY,
        "Reform Party",
        "founder-1",
        "lower taxes",
        LOCATION_GOVERNMENT_COMPLEX,
        now_tick=0,
    )
    assert org.members == {"founder-1": "founder"}
    assert org.active is True
    assert recorder.of_type(EVENT_ORGANIZATION_REGISTERED)


@pytest.mark.asyncio
async def test_register_duplicate_name_rejected() -> None:
    registry, _ = await make_registry()
    await registry.register_organization(
        OrganizationKind.RELIGION,
        "The Path",
        "founder-1",
        "c",
        LOCATION_GOVERNMENT_COMPLEX,
        now_tick=0,
    )

    with pytest.raises(DuplicateOrganizationName):
        await registry.register_organization(
            OrganizationKind.SOCIETY,
            "The Path",
            "founder-2",
            "different charter",
            LOCATION_GOVERNMENT_COMPLEX,
            now_tick=1,
        )


@pytest.mark.asyncio
async def test_add_member() -> None:
    recorder = EventRecorder()
    registry, _ = await make_registry(recorder)
    org = await registry.register_organization(
        OrganizationKind.SOCIETY,
        "Builders Guild",
        "founder-1",
        "c",
        LOCATION_GOVERNMENT_COMPLEX,
        now_tick=0,
    )

    updated = await registry.add_member(org.org_id, "agent-2", now_tick=1)
    assert updated.members["agent-2"] == "member"
    assert recorder.of_type(EVENT_ORGANIZATION_MEMBER_ADDED)


@pytest.mark.asyncio
async def test_remove_member() -> None:
    recorder = EventRecorder()
    registry, _ = await make_registry(recorder)
    org = await registry.register_organization(
        OrganizationKind.SOCIETY,
        "Builders Guild",
        "founder-1",
        "c",
        LOCATION_GOVERNMENT_COMPLEX,
        now_tick=0,
    )
    await registry.add_member(org.org_id, "agent-2", now_tick=1)

    updated = await registry.remove_member(org.org_id, "agent-2", now_tick=2)
    assert "agent-2" not in updated.members
    assert recorder.of_type(EVENT_ORGANIZATION_MEMBER_REMOVED)


@pytest.mark.asyncio
async def test_remove_non_member_raises() -> None:
    registry, _ = await make_registry()
    org = await registry.register_organization(
        OrganizationKind.SOCIETY,
        "Builders Guild",
        "founder-1",
        "c",
        LOCATION_GOVERNMENT_COMPLEX,
        now_tick=0,
    )

    with pytest.raises(NotOrganizationMember):
        await registry.remove_member(org.org_id, "never-joined", now_tick=1)


@pytest.mark.asyncio
async def test_load_unknown_organization_raises() -> None:
    registry, _ = await make_registry()
    with pytest.raises(OrganizationNotFound):
        await registry.add_member("does-not-exist", "agent-1", now_tick=0)


@pytest.mark.asyncio
async def test_list_by_kind_filters() -> None:
    registry, _ = await make_registry()
    await registry.register_organization(
        OrganizationKind.CULT, "Cult A", "f1", "c", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )
    await registry.register_organization(
        OrganizationKind.PARTY, "Party A", "f2", "c", LOCATION_GOVERNMENT_COMPLEX, now_tick=0
    )

    cults = await registry.list_by_kind(OrganizationKind.CULT)
    assert [o.name for o in cults] == ["Cult A"]
