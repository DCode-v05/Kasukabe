"""define_tool + approve/reject/revoke_patent: DESIGN.md section 2's
meta-tool, end to end - file, approve, invoke a composite tool under the
creator's identity, revoke."""

from __future__ import annotations

import pytest

from world.charter.models import GovernmentState
from world.tools.errors import ToolNotFoundError, ToolValidationError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_PATENT_OFFICE
from world.tools.tests.conftest import move_agent, spawn_agent


async def make_minister(world, agent_id: str, location_id: str = LOCATION_GOVERNMENT_COMPLEX) -> None:
    await spawn_agent(world, agent_id, location_id)
    await world.charter.store.set_government_state(GovernmentState(pm_agent_id=agent_id, minister_agent_ids=[]))


@pytest.mark.asyncio
async def test_define_tool_files_a_pending_application_and_charges_fee(world, registry) -> None:
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    charter = await world.charter.store.get_charter()
    balance_before = world.ledger.sim_balance("inventor")

    result = await registry.execute(
        world,
        "inventor",
        "define_tool",
        {
            "name": "commute",
            "description": "walk somewhere",
            "param_schema": {"dest": "str"},
            "steps": [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}],
        },
    )

    assert result.result["application_id"]
    assert world.ledger.sim_balance("inventor") == balance_before - charter.patent_filing_fee_sim
    assert world.custom_tools["commute"].approved is False
    assert registry.get("commute") is None  # not callable until approved


@pytest.mark.asyncio
async def test_invented_tool_uncallable_before_approval(world, registry) -> None:
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    await registry.execute(
        world,
        "inventor",
        "define_tool",
        {
            "name": "commute2",
            "description": "walk somewhere",
            "param_schema": {"dest": "str"},
            "steps": [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}],
        },
    )
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "inventor", "commute2", {"dest": "park"})


@pytest.mark.asyncio
async def test_define_tool_rejects_non_whitelisted_step(world, registry) -> None:
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    with pytest.raises(ToolValidationError):
        await registry.execute(
            world,
            "inventor",
            "define_tool",
            {
                "name": "cheat",
                "description": "mint money",
                "param_schema": {},
                "steps": [{"tool_name": "mint_money", "params": {}}],
            },
        )


@pytest.mark.asyncio
async def test_define_tool_cannot_compose_define_tool_itself(world, registry) -> None:
    """The whitelist is frozen before define_tool is registered
    (bootstrap.py), so define_tool can never appear as a step -
    DESIGN.md's "no new physics possible by construction" for the
    meta-tool's own reach, not just ordinary primitives."""
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    with pytest.raises(ToolValidationError):
        await registry.execute(
            world,
            "inventor",
            "define_tool",
            {
                "name": "meta_meta",
                "description": "invent an inventor",
                "param_schema": {},
                "steps": [{"tool_name": "define_tool", "params": {}}],
            },
        )


@pytest.mark.asyncio
async def test_approve_reject_revoke_patent_lifecycle(world, registry) -> None:
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    filed = await registry.execute(
        world,
        "inventor",
        "define_tool",
        {
            "name": "commute3",
            "description": "walk somewhere",
            "param_schema": {"dest": "str"},
            "steps": [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}],
        },
    )
    application_id = filed.result["application_id"]

    await make_minister(world, "minister1")
    approved = await registry.execute(world, "minister1", "approve_patent", {"application_id": application_id})
    assert approved.result["tool_name"] == "commute3"
    assert registry.get("commute3") is not None
    assert world.custom_tools["commute3"].approved is True

    revoked = await registry.execute(
        world,
        "minister1",
        "revoke_patent",
        {"patent_id": approved.result["patent_id"], "tool_name": "commute3", "reason": "abuse"},
    )
    assert revoked.result["patent_id"] == approved.result["patent_id"]
    assert registry.get("commute3") is None


@pytest.mark.asyncio
async def test_reject_patent_leaves_tool_unregistered(world, registry) -> None:
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    filed = await registry.execute(
        world,
        "inventor",
        "define_tool",
        {
            "name": "commute4",
            "description": "walk somewhere",
            "param_schema": {"dest": "str"},
            "steps": [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}],
        },
    )
    await make_minister(world, "minister1")

    rejected = await registry.execute(
        world,
        "minister1",
        "reject_patent",
        {"application_id": filed.result["application_id"], "reason": "too similar to move"},
    )
    assert rejected.result["application_id"] == filed.result["application_id"]
    assert registry.get("commute4") is None


@pytest.mark.asyncio
async def test_a_composite_tool_moves_whoever_called_it(world, registry) -> None:
    """CHANGED BY C1, deliberately, and against a DESIGN.md sentence.

    DESIGN.md section 2 says an invented tool "runs under the creator's
    identity/wallet", and this test used to prove a stranger could call
    `commute5` and thereby move the INVENTOR across town from a cafe they
    had never left.

    That sentence was written when the creator was the only person who
    would ever call one. The tool-text addendum's section 9 says the
    opposite for a licensee - "they call it under their own name at their
    own cost" - and it has to, or a licence grants nothing and a
    licensee's call spends the inventor's money. Both cannot hold, so:

      * Only the owner, a current licensee, or somebody taught their own
        copy may call an invented tool at all (see `is_visible`). A
        stranger gets ToolNotFoundError, which is the same answer they
        already got for a confidential tool.
      * The steps run under the CALLER. For the creator's own calls that
        is the same id, so DESIGN.md's sentence still describes every call
        the creator makes.

    The old behaviour was also the reason C4 - "a tool held by three
    residents" - could not have a meaningful numerator: every tool was
    held by everybody.
    """
    await spawn_agent(world, "inventor", LOCATION_PATENT_OFFICE, age_years=25.0)
    filed = await registry.execute(
        world,
        "inventor",
        "define_tool",
        {
            "name": "commute5",
            "description": "walk somewhere",
            "param_schema": {"dest": "str"},
            "steps": [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}],
        },
    )
    await make_minister(world, "minister1")
    await registry.execute(
        world, "minister1", "approve_patent", {"application_id": filed.result["application_id"]}
    )

    # A stranger cannot call it at all. Before C1 this call succeeded and
    # teleported the inventor out of the patent office.
    await spawn_agent(world, "caller", "cafe")
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "caller", "commute5", {"dest": "temple"})
    assert world.location_of("inventor") == LOCATION_PATENT_OFFICE
    assert world.location_of("caller") == "cafe"

    # Licensed, it works, and it moves the LICENSEE.
    await move_agent(world, "caller", LOCATION_PATENT_OFFICE)
    await registry.execute(world, "inventor", "license_tool", {
        "tool_name": "commute5", "licensee_id": "caller", "term_days": 1,
    })
    result = await registry.execute(world, "caller", "commute5", {"dest": "temple"})

    assert world.location_of("caller") == "temple"
    assert world.location_of("inventor") == LOCATION_PATENT_OFFICE
    assert result.result["steps_executed"] == 1
    assert any(e.type == "business.invented_tool.invoked" for e in result.events)
    assert any(e.type == "agent.moved" for e in result.events)
