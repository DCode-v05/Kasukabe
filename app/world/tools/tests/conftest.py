"""Shared fixtures for world/tools/'s test suite.

Every test builds its own `WorldState`/`ToolRegistry` via `build_world()`
(bootstrap.py) - the real wiring, not a stub - then uses the helpers here
to spawn agents (life/ledger/location, all three) and place them.
Nothing here calls a real LLM or a real Bedrock/broker endpoint
(app/CONVENTIONS.md's test rule); every sibling system here (ledger,
charter, life) is already itself fully in-memory and synchronous-under-
the-hood, so there is nothing to mock.
"""

from __future__ import annotations

import pytest_asyncio

from world.life.models import AgentLifeRecord, MaritalStatus, Sex
from world.tools.bootstrap import build_world
from world.tools.registry import ToolRegistry
from world.tools.state import WorldState

STARTING_SIM_BALANCE = 50_000
STARTING_USD_CENTS = 0


async def spawn_agent(
    world: WorldState,
    agent_id: str,
    location_id: str,
    *,
    age_years: float = 30.0,
    sex: Sex = Sex.MALE,
    household_id: str | None = None,
    marital_status: MaritalStatus = MaritalStatus.SINGLE,
    spouse_id: str | None = None,
    starting_sim: int = STARTING_SIM_BALANCE,
) -> AgentLifeRecord:
    """Create one agent across every system a tool handler might touch:
    a life record (world.agents), a ledger account (money), and a
    location (via the real agent.spawned event, so LocationIndex updates
    itself exactly the way it would in the running system).
    """
    record = AgentLifeRecord(
        agent_id=agent_id,
        age_years=age_years,
        sex=sex,
        household_id=household_id,
        marital_status=marital_status,
        spouse_id=spouse_id,
    )
    world.agents[agent_id] = record
    world.ledger.open_agent(agent_id, starting_usd_cents=STARTING_USD_CENTS, starting_sim=starting_sim)
    await world.event_bus.emit(
        "agent.spawned", tick=world.tick(), payload={"agent_id": agent_id, "location_id": location_id}
    )
    return record


async def move_agent(world: WorldState, agent_id: str, location_id: str) -> None:
    await world.event_bus.emit(
        "agent.moved", tick=world.tick(), payload={"agent_id": agent_id, "location_id": location_id}
    )


@pytest_asyncio.fixture
async def world_and_registry() -> tuple[WorldState, ToolRegistry]:
    return await build_world(genesis_tick=0, seed=1234)


@pytest_asyncio.fixture
async def world(world_and_registry: tuple[WorldState, ToolRegistry]) -> WorldState:
    return world_and_registry[0]


@pytest_asyncio.fixture
async def registry(world_and_registry: tuple[WorldState, ToolRegistry]) -> ToolRegistry:
    return world_and_registry[1]


def action_cost(tool_name: str, params: dict | None = None, *, proficiency: int = 0) -> int:
    """What `dispatch()` now charges the actor for one call of `tool_name`.

    Tests that assert on a balance after a successful action have to account
    for this, because acting is no longer free (Part A). Read from the live
    schedule rather than hardcoded, so a repriced SCHEDULE_TABLE row moves the
    expectation with it instead of turning into a puzzle about a magic number.
    """
    from world.ledger.action_costs import DEFAULT_SCHEDULE, ActionActor

    return DEFAULT_SCHEDULE.cost_for(
        tool_name, params or {}, ActionActor("test", skill_proficiency=proficiency)
    )



# TOOL-27. Re-exported so this package's modules can write
# `pytestmark = needs_the_map`; the reasoning lives in world/tests/map_assets.py.
from world.tests.map_assets import (  # noqa: E402
    MAP_ASSETS_PRESENT,
    NO_MAP_REASON,
    needs_the_map,
)

__all__ = ["MAP_ASSETS_PRESENT", "NO_MAP_REASON", "needs_the_map", "spawn_agent"]


def in_conversation(world, *agent_ids: str) -> str:
    """Open a conversation where the first of them stands, all on its roster.

    What `say` addressed to somebody does in one call. A sale is agreed
    between two people who are talking (`commerce._require_dealing`), so a
    test that means to exercise a purchase has to put its buyer and seller -
    or the buyer and whoever is minding the shop - in one first.
    """
    return world.open_conversation(
        place=world.location_of(agent_ids[0]), topic="",
        participants=list(agent_ids), now=world.tick(),
    )
