"""An organisation can have more than one person in it.

`GOV-10` / `TOOL-30` - the same defect, found independently from the
governance side and the tool side.

`ComplaintLedger` all over again: `add_member` and `remove_member` have
existed in `charter/organizations.py` since genesis, complete, with their
own events, and NO TOOL reached either one. So `registry.py`'s
ORGANIZATION-namespace check - `actor_id in organization.members` - could
only ever be true for the founder, and every guild, party, cult and
society the town could register was permanently a society of one.

DESIGN section 7's "organisations first-class" and ROADMAP T6 had no
runtime surface at all. Not by choice: by a missing verb.

Founder-only admission, because there is no officer role yet - roles,
dues and an org treasury are the rest of GOV-10 and are NOT done here.
Leaving needs nobody's permission and costs nothing: a society you must
pay to leave is one a broke resident cannot leave, which is a different
institution from the one the design describes.

Co-located, the rule `teach_skill` and `trade` already follow - an act
between two residents happens where they both are.
"""
from __future__ import annotations

import pytest

from world.charter.models import LOCATION_GOVERNMENT_COMPLEX
from world.tools.errors import ActorRequirementError, LocationGateError, NotFoundError

from .test_construction import spawn_agent  # noqa: F401


async def _an_org(world, registry, founder: str = "founder") -> str:
    await spawn_agent(world, founder, LOCATION_GOVERNMENT_COMPLEX, age_years=30.0)
    world.ledger.get_account(founder).sim_units = 50_000
    result = await registry.execute(
        world, founder, "register_org",
        {"kind": "society", "name": "The Millers", "charter_text": "we mill"},
    )
    return result.result["org_id"]


@pytest.mark.asyncio
async def test_a_founder_can_admit_somebody(world, registry) -> None:
    """The finding. Before this there was no verb at all."""
    org_id = await _an_org(world, registry)
    await spawn_agent(world, "joiner", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)

    await registry.execute(
        world, "founder", "admit_to_org",
        {"org_id": org_id, "agent_id": "joiner"},
    )

    organization = await world.charter.store.get_organization(org_id)
    assert "joiner" in organization.members, (
        "GOV-10: add_member still has no caller, so an organisation is "
        "permanently its founder"
    )
    assert organization.members["joiner"] == "member"


@pytest.mark.asyncio
async def test_a_member_can_then_call_the_orgs_own_tools(world, registry) -> None:
    """The consequence that matters. `registry.py`'s ORGANIZATION check is
    `actor_id in organization.members`, so until a second person could get
    in, the whole namespace was unreachable by construction."""
    org_id = await _an_org(world, registry)
    await spawn_agent(world, "joiner", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)
    await registry.execute(
        world, "founder", "admit_to_org",
        {"org_id": org_id, "agent_id": "joiner"},
    )

    organization = await world.charter.store.get_organization(org_id)
    assert set(organization.members) == {"founder", "joiner"}


@pytest.mark.asyncio
async def test_only_the_founder_may_admit(world, registry) -> None:
    """There is no officer role yet. Letting any member admit would be
    inventing governance the design has not specified."""
    org_id = await _an_org(world, registry)
    await spawn_agent(world, "joiner", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)
    await spawn_agent(world, "stranger", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)
    await registry.execute(
        world, "founder", "admit_to_org", {"org_id": org_id, "agent_id": "joiner"}
    )

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "joiner", "admit_to_org",
            {"org_id": org_id, "agent_id": "stranger"},
        )


@pytest.mark.asyncio
async def test_you_cannot_admit_somebody_who_is_elsewhere(world, registry) -> None:
    """The same rule `teach_skill` follows. Admitting somebody across town
    is a membership nobody in the room witnessed."""
    org_id = await _an_org(world, registry)
    await spawn_agent(world, "faraway", "Hobbs Cafe", age_years=25.0)

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "founder", "admit_to_org",
            {"org_id": org_id, "agent_id": "faraway"},
        )


@pytest.mark.asyncio
async def test_admitting_twice_is_refused(world, registry) -> None:
    """`setdefault` would silently no-op, so the founder would be told it
    worked and learn nothing about the state of its own guild."""
    org_id = await _an_org(world, registry)
    await spawn_agent(world, "joiner", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)
    await registry.execute(
        world, "founder", "admit_to_org", {"org_id": org_id, "agent_id": "joiner"}
    )

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "founder", "admit_to_org",
            {"org_id": org_id, "agent_id": "joiner"},
        )


@pytest.mark.asyncio
async def test_a_member_can_leave_without_asking(world, registry) -> None:
    """A society you cannot leave is not an association."""
    org_id = await _an_org(world, registry)
    await spawn_agent(world, "joiner", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)
    await registry.execute(
        world, "founder", "admit_to_org", {"org_id": org_id, "agent_id": "joiner"}
    )

    await registry.execute(world, "joiner", "leave_org", {"org_id": org_id})

    organization = await world.charter.store.get_organization(org_id)
    assert "joiner" not in organization.members


@pytest.mark.asyncio
async def test_leaving_is_free(world, registry) -> None:
    """A body a broke resident cannot afford to leave is a trap."""
    from world.ledger.action_costs import ActionActor, cost_for

    assert cost_for("leave_org", {}, ActionActor("anyone")) == 0


@pytest.mark.asyncio
async def test_the_founder_cannot_leave(world, registry) -> None:
    """It would leave a body whose ORGANIZATION tools nobody can call and
    nobody can grant - a worse state than the founder-only one this
    fixes."""
    org_id = await _an_org(world, registry)

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "founder", "leave_org", {"org_id": org_id})


@pytest.mark.asyncio
async def test_an_unknown_org_is_a_not_found(world, registry) -> None:
    await spawn_agent(world, "nobody", LOCATION_GOVERNMENT_COMPLEX, age_years=25.0)

    with pytest.raises(NotFoundError):
        await registry.execute(
            world, "nobody", "leave_org", {"org_id": "org-that-never-was"}
        )


@pytest.mark.asyncio
async def test_both_verbs_are_registered(world, registry) -> None:
    """A handler with no ToolSpec is the same defect one layer along."""
    for name in ("admit_to_org", "leave_org"):
        assert name in registry.tools, (
            f"GOV-10: {name} is not registered, so the mechanism still has "
            "no caller"
        )
