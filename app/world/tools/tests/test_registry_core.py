"""Tests for ToolRegistry's own dispatch order and atomicity, independent
of any one primitive: actor existence, visibility/ACL, actor
requirements, params validation, location gates, and the
snapshot/rollback guarantee.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from world.charter.models import License
from world.tools.errors import (
    ActorNotFoundError,
    ActorRequirementError,
    LocationGateError,
    ToolNotFoundError,
    ToolValidationError,
)
from world.tools.registry import ToolOutcome, ToolRegistry, ToolSpec
from world.tools.tests.conftest import spawn_agent


class NoopParams(BaseModel):
    value: int = 1


async def noop_handler(world, agent, params) -> ToolOutcome:
    return ToolOutcome(result={"value": params.value})


@pytest.mark.asyncio
async def test_execute_rejects_unknown_actor(world, registry) -> None:
    with pytest.raises(ActorNotFoundError):
        await registry.execute(world, "ghost", "move", {"destination_location_id": "park"})


@pytest.mark.asyncio
async def test_execute_rejects_unknown_tool(world, registry) -> None:
    await spawn_agent(world, "a1", "park")
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "a1", "does_not_exist", {})


@pytest.mark.asyncio
async def test_government_tool_invisible_to_non_incumbent_reads_as_not_found(world, registry) -> None:
    await spawn_agent(world, "a1", "government_complex")
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "a1", "approve_patent", {"application_id": "whatever"})


@pytest.mark.asyncio
async def test_actor_requirement_min_age_blocks_minor(world, registry) -> None:
    await spawn_agent(world, "kid", "government_complex", age_years=10.0)
    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world,
            "kid",
            "register_business",
            {"business_id": "kids-lemonade", "name": "Lemonade", "business_type": "grocery"},
        )


@pytest.mark.asyncio
async def test_actor_requirement_dormant_agent_blocked(world, registry) -> None:
    agent = await spawn_agent(world, "poor", "park")
    world.agents["poor"] = agent.model_copy(
        update={"dormancy": agent.dormancy.model_copy(update={"is_dormant": True})}
    )
    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "poor", "move", {"destination_location_id": "cafe"})


@pytest.mark.asyncio
async def test_params_validation_error_is_typed(world, registry) -> None:
    await spawn_agent(world, "a1", "park")
    with pytest.raises(ToolValidationError):
        await registry.execute(world, "a1", "move", {})


@pytest.mark.asyncio
async def test_location_gate_blocks_wrong_place(world, registry) -> None:
    await spawn_agent(world, "a1", "park")
    with pytest.raises(LocationGateError):
        await registry.execute(world, "a1", "pray", {})


@pytest.mark.asyncio
async def test_location_gate_allows_right_place(world, registry) -> None:
    await spawn_agent(world, "a1", "temple")
    result = await registry.execute(world, "a1", "pray", {"message": "hello"})
    assert result.result["request_id"]


@pytest.mark.asyncio
async def test_failed_handler_rolls_back_earlier_mutations_in_the_same_call(world, registry) -> None:
    """build_structure debits timber, then debits the chosen metal ore.
    Give the builder enough timber but zero iron_ore: the first debit
    succeeds, the second raises, and the whole call must leave the
    builder's timber balance untouched - proof that ToolRegistry.execute()
    rolls back every tools-owned mutation from the call, not just the one
    that raised.
    """
    await spawn_agent(world, "builder", "plot-1")
    world.inventory.credit("builder", "timber", 1_000)
    await world.charter.store.put_license(
        License(
            license_id="lic-1",
            license_type="builder_license",
            holder_agent_id="builder",
            fee_paid_sim=0,
            issued_by_agent_id="genesis",
            issued_tick=0,
        )
    )
    from world.tools.models import Plot

    footprint = frozenset({(0, 0), (1, 0)})
    world.plots["plot-1"] = Plot(
        plot_id="plot-1", owner_agent_id="builder", location_id="plot-1", footprint=footprint, permitted=True
    )

    with pytest.raises(Exception):
        await registry.execute(
            world,
            "builder",
            "build_structure",
            {"plot_id": "plot-1", "footprint": [[0, 0], [1, 0]], "purpose": "workshop", "metal_kind": "iron"},
        )

    assert world.inventory.quantity("builder", "timber") == 1_000
    assert world.buildings == {}


@pytest.mark.asyncio
async def test_successful_call_commits_and_publishes_event(world, registry) -> None:
    await spawn_agent(world, "a1", "farmland:wheat")
    seen_events = []

    async def record_event(event) -> None:
        seen_events.append(event)

    world.event_bus.subscribe("world", record_event)

    result = await registry.execute(world, "a1", "harvest", {"crop": "wheat", "quantity": 3})
    assert result.result == {"item": "wheat", "quantity": 3}
    assert world.inventory.quantity("a1", "wheat") == 3
    assert any(e.type == "world.resource.harvested" for e in result.events)
    assert any(e.type == "world.resource.harvested" for e in seen_events)


def test_register_two_tools_with_same_name_raises() -> None:
    registry = ToolRegistry()
    spec = ToolSpec(name="dupe", description="", params_model=NoopParams, handler=noop_handler)
    registry.register(spec)
    with pytest.raises(ValueError):
        registry.register(spec)


def test_freeze_primitives_only_captures_flagged_specs() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="prim",
            description="",
            params_model=NoopParams,
            handler=noop_handler,
            is_primitive=True,
        )
    )
    registry.freeze_primitives()
    registry.register(
        ToolSpec(
            name="invented",
            description="",
            params_model=NoopParams,
            handler=noop_handler,
            is_primitive=False,
        )
    )
    assert registry.primitive_names == frozenset({"prim"})
