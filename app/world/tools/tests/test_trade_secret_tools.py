"""define_business_tool / define_organization_tool: DESIGN.md section 2's
namespace rules made real for BUSINESS and ORGANIZATION, end to end.

Before these tools existed, `Business.employee_agent_ids` could never be
non-empty and no BUSINESS/ORGANIZATION-namespace `ToolSpec` was ever
registered, so registry.py's `is_visible` branches for those two
namespaces were correct but dead code - nothing ever exercised them. These
tests are that exercise, and DESIGN.md section 2's "non-members cannot see
a confidential tool exists" existence-leak requirement for both namespaces
(GOVERNMENT's own version is already covered in test_registry_core.py and
test_civic.py).
"""

from __future__ import annotations

import pytest

from world.charter.models import OrganizationKind
from world.tools.errors import ActorRequirementError, ToolNotFoundError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.tests.conftest import spawn_agent

TRADE_SECRET_STEPS = [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}]


async def register_business(world, registry, owner_id: str, business_id: str = "biz-1"):
    await spawn_agent(world, owner_id, LOCATION_GOVERNMENT_COMPLEX)
    return await registry.execute(
        world, owner_id, "register_business", {"business_id": business_id, "name": "Biz", "business_type": "grocery"}
    )


async def register_org(world, registry, founder_id: str, org_id_out: dict):
    await spawn_agent(world, founder_id, LOCATION_GOVERNMENT_COMPLEX)
    result = await registry.execute(
        world,
        founder_id,
        "register_org",
        {"kind": OrganizationKind.CULT.value, "name": "The Riverside Circle", "charter_text": "secrets"},
    )
    org_id_out["org_id"] = result.result["org_id"]


# -- define_business_tool -----------------------------------------------


@pytest.mark.asyncio
async def test_define_business_tool_is_live_immediately_no_approval_step(world, registry) -> None:
    await register_business(world, registry, "owner")

    result = await registry.execute(
        world,
        "owner",
        "define_business_tool",
        {
            "business_id": "biz-1",
            "name": "secret_recipe",
            "description": "the house blend",
            "param_schema": {"dest": "str"},
            "steps": TRADE_SECRET_STEPS,
        },
    )

    assert result.result == {"tool_name": "secret_recipe"}
    # No patent office, no PENDING state, no fee - DESIGN.md: "no patent
    # office needed" - contrast define_tool's own test, which asserts the
    # opposite (registry.get(name) is None until approve_patent runs).
    assert world.custom_tools["secret_recipe"].approved is True
    assert registry.get("secret_recipe") is not None


@pytest.mark.asyncio
async def test_define_business_tool_only_owner_can_call_and_gets_no_fee_charged(world, registry) -> None:
    await register_business(world, registry, "owner")
    balance_before = world.ledger.sim_balance("owner")

    await registry.execute(
        world,
        "owner",
        "define_business_tool",
        {"business_id": "biz-1", "name": "secret_recipe2", "description": "d", "param_schema": {"dest": "str"}, "steps": TRADE_SECRET_STEPS},
    )

    assert world.ledger.sim_balance("owner") == balance_before


@pytest.mark.asyncio
async def test_define_business_tool_rejected_for_non_owner(world, registry) -> None:
    await register_business(world, registry, "owner")
    await spawn_agent(world, "impostor", "park")

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "impostor",
            "define_business_tool",
            {"business_id": "biz-1", "name": "stolen_recipe", "description": "d", "param_schema": {}, "steps": TRADE_SECRET_STEPS},
        )


@pytest.mark.asyncio
async def test_business_trade_secret_tool_existence_leak(world, registry) -> None:
    """DESIGN.md section 2: "Non-members cannot see a confidential tool
    exists" for BUSINESS the same as GOVERNMENT - an outsider calling a
    trade secret's tool name gets the identical ToolNotFoundError a truly
    unknown tool name would, never a distinguishable "forbidden."
    """
    await register_business(world, registry, "owner")
    await registry.execute(
        world,
        "owner",
        "define_business_tool",
        {"business_id": "biz-1", "name": "secret_recipe3", "description": "d", "param_schema": {"dest": "str"}, "steps": TRADE_SECRET_STEPS},
    )
    await spawn_agent(world, "outsider", "park")

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "outsider", "secret_recipe3", {"dest": "park"})

    # Owner can call it.
    result = await registry.execute(world, "owner", "secret_recipe3", {"dest": "cafe"})
    assert result.result["steps_executed"] == 1


@pytest.mark.asyncio
async def test_business_trade_secret_tool_visible_to_hired_employee_and_lost_when_fired(world, registry) -> None:
    await register_business(world, registry, "owner")
    await registry.execute(
        world,
        "owner",
        "define_business_tool",
        {"business_id": "biz-1", "name": "secret_recipe4", "description": "d", "param_schema": {"dest": "str"}, "steps": TRADE_SECRET_STEPS},
    )
    await spawn_agent(world, "staffer", "park")

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "staffer", "secret_recipe4", {"dest": "cafe"})

    await registry.execute(world, "owner", "hire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer", "employee_consent": True})
    result = await registry.execute(world, "staffer", "secret_recipe4", {"dest": "park"})
    assert result.result["steps_executed"] == 1

    await registry.execute(world, "owner", "fire_employee", {"business_id": "biz-1", "employee_agent_id": "staffer"})
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "staffer", "secret_recipe4", {"dest": "cafe"})


@pytest.mark.asyncio
async def test_business_trade_secret_tool_transfers_with_business_sale(world, registry) -> None:
    await register_business(world, registry, "seller")
    await registry.execute(
        world,
        "seller",
        "define_business_tool",
        {"business_id": "biz-1", "name": "secret_recipe5", "description": "d", "param_schema": {"dest": "str"}, "steps": TRADE_SECRET_STEPS},
    )
    await spawn_agent(world, "buyer", "park")

    await registry.execute(world, "seller", "transfer_business", {"business_id": "biz-1", "new_owner_agent_id": "buyer"})

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "seller", "secret_recipe5", {"dest": "cafe"})
    result = await registry.execute(world, "buyer", "secret_recipe5", {"dest": "park"})
    assert result.result["steps_executed"] == 1


# -- define_organization_tool --------------------------------------------


@pytest.mark.asyncio
async def test_define_organization_tool_only_founder_can_call(world, registry) -> None:
    org_id_out: dict = {}
    await register_org(world, registry, "founder", org_id_out)
    await spawn_agent(world, "outsider", "park")

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "outsider",
            "define_organization_tool",
            {"org_id": org_id_out["org_id"], "name": "stolen_rite", "description": "d", "param_schema": {}, "steps": TRADE_SECRET_STEPS},
        )


@pytest.mark.asyncio
async def test_organization_tool_existence_leak_and_member_gating(world, registry) -> None:
    org_id_out: dict = {}
    await register_org(world, registry, "founder", org_id_out)
    org_id = org_id_out["org_id"]
    await registry.execute(
        world,
        "founder",
        "define_organization_tool",
        {"org_id": org_id, "name": "secret_rite", "description": "d", "param_schema": {"dest": "str"}, "steps": TRADE_SECRET_STEPS},
    )
    await spawn_agent(world, "outsider", "park")
    await spawn_agent(world, "initiate", "park")

    # Non-member: identical not-found error as an unknown tool name.
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "outsider", "secret_rite", {"dest": "park"})

    # Founder (already a member) can call it.
    result = await registry.execute(world, "founder", "secret_rite", {"dest": "cafe"})
    assert result.result["steps_executed"] == 1

    # A new member gains access the instant they're added, live-checked -
    # no re-registration or re-approval needed.
    await world.charter.organizations.add_member(org_id, "initiate", world.tick())
    result = await registry.execute(world, "initiate", "secret_rite", {"dest": "park"})
    assert result.result["steps_executed"] == 1

    # Removed member loses access immediately.
    await world.charter.organizations.remove_member(org_id, "initiate", world.tick())
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "initiate", "secret_rite", {"dest": "cafe"})
