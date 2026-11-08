"""A world tool the world stops offering must leave the engine's list too.

`refresh_world_tool_registry` ADDED tools and RE-DESCRIBED them, and never
removed one. So a tool the world had offered once stayed in front of the
model forever - after an election took a seat away, and now after a
conversation ends and `buy` is no longer offered (the operator's rule of
2026-09-21: `buy` is enabled only while the resident is in a conversation).
The world would refuse the call; the model would still be shown the verb and
spend a paid call finding out.

Only WORLD tools are removed - those carrying `WORLD_TOOL_NAME_PREFIX`. The
engine registers its own tools (session log, memory stream) on the same
registry, and those are none of the world's business.
"""

from __future__ import annotations

import pytest

from src.sim.world_tools import (
    WORLD_TOOL_NAME_PREFIX,
    build_world_tool_registry,
    refresh_world_tool_registry,
)

from tests.test_the_registry_is_not_refetched_every_tick import BAKE, MINE, FakeWorld


async def _registry_offering(world: FakeWorld, rows):
    world.rows = [dict(r) for r in rows]
    client = world.client()
    registry = await build_world_tool_registry(client)
    return client, registry


@pytest.mark.asyncio
async def test_a_tool_the_world_stops_offering_is_removed():
    world = FakeWorld("agent-drop")
    client, registry = await _registry_offering(world, [MINE, BAKE])
    assert f"{WORLD_TOOL_NAME_PREFIX}bake" in registry.tools

    world.rows = [dict(MINE)]
    await refresh_world_tool_registry(client, registry, known_version=world.version, force=True)

    assert f"{WORLD_TOOL_NAME_PREFIX}bake" not in registry.tools, (
        "the world stopped offering `bake` and the model is still shown it"
    )
    assert f"{WORLD_TOOL_NAME_PREFIX}mine" in registry.tools


@pytest.mark.asyncio
async def test_it_comes_back_when_the_world_offers_it_again():
    world = FakeWorld("agent-return")
    client, registry = await _registry_offering(world, [MINE])

    world.rows = [dict(MINE), dict(BAKE)]
    await refresh_world_tool_registry(client, registry, known_version=world.version, force=True)

    assert f"{WORLD_TOOL_NAME_PREFIX}bake" in registry.tools


@pytest.mark.asyncio
async def test_the_engines_own_tools_are_never_removed():
    world = FakeWorld("agent-own")
    client, registry = await _registry_offering(world, [MINE, BAKE])
    registry.tools["session_log"] = registry.tools[f"{WORLD_TOOL_NAME_PREFIX}mine"]

    world.rows = [dict(MINE)]
    await refresh_world_tool_registry(client, registry, known_version=world.version, force=True)

    assert "session_log" in registry.tools


@pytest.mark.asyncio
async def test_an_unchanged_answer_removes_nothing():
    """A 304 carries no list, so it cannot be read as "offers nothing"."""
    world = FakeWorld("agent-same")
    client, registry = await _registry_offering(world, [MINE, BAKE])

    await refresh_world_tool_registry(client, registry, known_version=world.version, force=True)

    assert f"{WORLD_TOOL_NAME_PREFIX}bake" in registry.tools
    assert f"{WORLD_TOOL_NAME_PREFIX}mine" in registry.tools
