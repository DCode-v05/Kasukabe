"""TOOL-07: a confidential tool must not appear in the boot payload.

`tool_registry_rows` iterated `registry.tools.values()` with no visibility
check, so the `GET /tools/registry?agent_id=...` payload every engine boots
from listed all fifteen GOVERNMENT-namespace tools, every business's trade
secret and every neighbour's `toolify` invention - by name, by description
and, whenever the budget reached them, by full JSON schema - to residents
who may call none of them. Measured on this checkout before the fix: 88
rows served to a farmhand who is not in government, fifteen of them
government tools; after it, 73. DESIGN.md section 2 says "non-members
cannot see a confidential tool exists", and enforcement began at
`ToolRegistry.execute()` - call time, one refusal AFTER the resident had
already read the name, formed the call and paid for the turn. Worse, the
town contradicted itself: `describe_primitives` (meta.py) and
`authoring_context` (state.py) already filtered on `is_visible`, so the
vocabulary listing and the schema listing disagreed about which verbs the
world contains.

The fix is `ToolRegistry.visible_to` - the synchronous body that
`is_visible` now delegates to - applied inside `tool_registry_rows` before
the ENG-03 budget ranks anything. Synchronous because both hosts serve the
route from inside an `async def` that calls the builder without awaiting
it, and neither host is this agent's file to re-plumb.

The wiring test at the bottom is the one that matters: it drives the real
embedded FastAPI route from `world/run_genesis.py` over ASGI, because a
test that only calls `tool_registry_rows` directly would stay green if the
route stopped passing the resident's id and started serving every agent
the anonymous, unfiltered payload.
"""

from __future__ import annotations

import httpx
import pytest

from world.charter.models import GovernmentState, OrganizationKind
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.registry import ToolNamespace, tool_registry_rows
from world.tools.tests.conftest import spawn_agent

TRADE_SECRET_STEPS = [{"tool_name": "look_around", "params": {}}]


def _government_tool_names(registry) -> list[str]:
    return [
        spec.name
        for spec in registry.tools.values()
        if spec.namespace is ToolNamespace.GOVERNMENT
    ]


@pytest.mark.asyncio
async def test_a_resident_out_of_government_is_not_served_the_government_tools(
    world_and_registry,
) -> None:
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    government = _government_tool_names(registry)
    assert government, "the fixture registers no GOVERNMENT tools; nothing to leak"

    served = {row["name"] for row in tool_registry_rows(registry, world, "n01")}

    leaked = sorted(name for name in government if name in served)
    assert not leaked, (
        f"{len(leaked)} government tools served to a resident who holds no "
        f"seat: {leaked}. DESIGN.md section 2: non-members cannot see a "
        "confidential tool exists."
    )


@pytest.mark.asyncio
async def test_the_sitting_government_still_gets_its_own_tools(
    world_and_registry,
) -> None:
    """The other half of the rule: filtering that hid a verb from the
    minister who needs it would break governance instead of protecting it.
    """
    world, registry = world_and_registry
    await spawn_agent(world, "pm", LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=[])
    )

    served = {row["name"] for row in tool_registry_rows(registry, world, "pm")}

    for name in _government_tool_names(registry):
        assert name in served, (
            f"{name} was withheld from the sitting PM; the visibility filter "
            "is hiding the government's own verbs from the government"
        )


@pytest.mark.asyncio
async def test_a_trade_secret_does_not_reach_a_stranger(world_and_registry) -> None:
    """A BUSINESS-namespace tool: its owner sees it, the town does not."""
    world, registry = world_and_registry
    await spawn_agent(world, "owner", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "stranger", "Kasukabe Farmland")
    await registry.execute(
        world,
        "owner",
        "register_business",
        {"business_id": "biz-1", "name": "Biz", "business_type": "grocery"},
    )
    await registry.execute(
        world,
        "owner",
        "define_business_tool",
        {
            "business_id": "biz-1",
            "name": "house_blend",
            "description": "the recipe nobody else has",
            "param_schema": {},
            "steps": TRADE_SECRET_STEPS,
        },
    )

    to_owner = {row["name"] for row in tool_registry_rows(registry, world, "owner")}
    to_stranger = {
        row["name"] for row in tool_registry_rows(registry, world, "stranger")
    }

    assert "house_blend" in to_owner, "the owner cannot see its own trade secret"
    assert "house_blend" not in to_stranger, (
        "a business's trade secret was served to a resident who is neither "
        "its owner nor its employee"
    )


@pytest.mark.asyncio
async def test_an_organisation_tool_does_not_reach_a_non_member(
    world_and_registry,
) -> None:
    world, registry = world_and_registry
    await spawn_agent(world, "founder", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "outsider", "Kasukabe Farmland")
    registered = await registry.execute(
        world,
        "founder",
        "register_org",
        {
            "kind": OrganizationKind.CULT.value,
            "name": "The Riverside Circle",
            "charter_text": "secrets",
        },
    )
    await registry.execute(
        world,
        "founder",
        "define_organization_tool",
        {
            "org_id": registered.result["org_id"],
            "name": "the_evening_rite",
            "description": "what the circle does at dusk",
            "param_schema": {},
            "steps": TRADE_SECRET_STEPS,
        },
    )

    to_founder = {row["name"] for row in tool_registry_rows(registry, world, "founder")}
    to_outsider = {
        row["name"] for row in tool_registry_rows(registry, world, "outsider")
    }

    assert "the_evening_rite" in to_founder
    assert "the_evening_rite" not in to_outsider, (
        "an organisation's private tool was served to somebody outside it"
    )


@pytest.mark.asyncio
async def test_a_neighbours_invention_is_not_in_your_payload(
    world_and_registry,
) -> None:
    """Run 23's `registry_version: 77` after `mill_flour_run`: one
    resident's `toolify` invention, served by name to all twenty.
    """
    world, registry = world_and_registry
    await spawn_agent(world, "inventor", "Kasukabe Farmland")
    await spawn_agent(world, "neighbour", "Kasukabe Farmland")
    await registry.execute(
        world,
        "inventor",
        "toolify",
        {
            "name": "mill_flour_run",
            "description": "walk to the mill and look at it",
            "steps": [
                {"order": 1, "tool_name": "move",
                 "params": {"destination_location_id": "business:b_mill"}},
                {"order": 2, "tool_name": "look_around"},
            ],
        },
    )
    assert registry.get("mill_flour_run") is not None, (
        "toolify did not register the invention; this test proves nothing"
    )

    to_inventor = {
        row["name"] for row in tool_registry_rows(registry, world, "inventor")
    }
    to_neighbour = {
        row["name"] for row in tool_registry_rows(registry, world, "neighbour")
    }

    assert "mill_flour_run" in to_inventor
    assert "mill_flour_run" not in to_neighbour, (
        "a resident's invented tool was served to a neighbour who may not "
        "call it - the town pays for those tokens on every turn of every "
        "engine"
    )


@pytest.mark.asyncio
async def test_the_payload_and_the_vocabulary_listing_cannot_disagree(
    world_and_registry,
) -> None:
    """One rule, one body. The defect was two listings of the town's
    vocabulary answering "does this tool exist" differently, so the guard
    is that `visible_to` and the awaited `is_visible` agree on every spec.
    """
    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")
    await spawn_agent(world, "pm", LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=[])
    )

    from world.tools.registry import CONVERSATION_ENABLED_TOOLS

    for agent_id in ("n01", "pm"):
        served = {row["name"] for row in tool_registry_rows(registry, world, agent_id)}
        talking = bool(world.conversation_partners(agent_id))
        for spec in registry.tools.values():
            allowed = await registry.is_visible(spec, world, agent_id)
            assert registry.visible_to(spec, world, agent_id) is allowed, (
                f"visible_to and is_visible disagree about {spec.name} for "
                f"{agent_id}"
            )
            if spec.name in CONVERSATION_ENABLED_TOOLS and not talking:
                assert spec.name not in served, (
                    f"{spec.name} was offered to {agent_id}, who is talking to nobody"
                )
                continue
            assert (spec.name in served) is allowed, (
                f"{spec.name} is {'' if allowed else 'not '}callable by "
                f"{agent_id} but is {'not ' if spec.name not in served else ''}"
                "in the payload"
            )


@pytest.mark.asyncio
async def test_the_route_the_engine_actually_calls_filters(world_and_registry) -> None:
    """WIRING. `GET /tools/registry?agent_id=...` on the embedded app from
    world/run_genesis.py - the process a live run boots, the one all 250
    engines fetch their tool list from (engine/src/sim/world_tools.py sends
    `agent_id` on every fetch).

    Driven over ASGI rather than by calling the builder, because the
    builder being correct is not the property that matters: the route
    choosing to call it WITH the resident's id is. Delete the `agent_id`
    branch in run_genesis.py and this test goes red while a direct
    `tool_registry_rows` test stays green.
    """
    from world.run_genesis import (
        GenesisMetrics,
        RunnerCounters,
        build_embedded_world_app,
    )

    world, registry = world_and_registry
    await spawn_agent(world, "n01", "Kasukabe Farmland")

    app = build_embedded_world_app(
        world,
        registry,
        world.ledger,
        GenesisMetrics(world.ledger, world, RunnerCounters()),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://world.test"
    ) as client:
        response = await client.get("/tools/registry", params={"agent_id": "n01"})

    assert response.status_code == 200, response.text
    served = {row["name"] for row in response.json()}
    assert "look_around" in served, (
        "the route served no ordinary verbs at all; this test is not "
        "reaching the real payload"
    )

    leaked = sorted(
        name for name in _government_tool_names(registry) if name in served
    )
    assert not leaked, (
        f"the live route served {len(leaked)} government tools to a resident "
        f"with no seat: {leaked}"
    )
