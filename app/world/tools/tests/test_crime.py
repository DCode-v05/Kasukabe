"""steal's dice rolls come from world.rng (an injected random.Random), so
these tests re-seed it to fixed values and assert on exact outcomes
rather than probabilities - CONVENTIONS.md's "fast, free, and
deterministic." seed=1 rolls (0.134, 0.847): success (< 0.70), not
witnessed (0.847 > 0.30). seed=99 rolls a failure - see the module-level
check below for exactly which seed was picked for which behavior.
"""

from __future__ import annotations

import random

import pytest

from world.tools.errors import ActorRequirementError, InsufficientInventoryError, LocationGateError
from world.tools.models import Storage
from world.tools.tests.conftest import move_agent, spawn_agent


async def setup_unattended_stash(world, victim_id: str = "victim", storage_id: str = "stash") -> None:
    await spawn_agent(world, victim_id, "market")
    world.storages[storage_id] = Storage(storage_id=storage_id, owner_id=victim_id, location_id="market")
    world.inventory.credit(storage_id, "gold_jewelry", 5)
    await move_agent(world, victim_id, "elsewhere")


@pytest.mark.asyncio
async def test_steal_success_and_not_witnessed_with_seed_one(world, registry) -> None:
    world.rng = random.Random(1)
    await setup_unattended_stash(world)
    await spawn_agent(world, "thief", "market")

    result = await registry.execute(
        world, "thief", "steal", {"storage_id": "stash", "item": "gold_jewelry", "quantity": 2}
    )

    assert result.result["succeeded"] is True
    assert result.result["witnessed"] is False
    assert world.inventory.quantity("thief", "gold_jewelry") == 2
    assert world.inventory.quantity("stash", "gold_jewelry") == 3
    assert world.crime_cases[0].outcome == "succeeded"
    assert any(e.type == "crime.theft.attempted" for e in result.events)
    assert not any(e.type == "crime.theft.witnessed" for e in result.events)


@pytest.mark.asyncio
async def test_steal_failure_leaves_item_in_place(world, registry) -> None:
    # seed 2's first two draws are both above their thresholds: fails and
    # goes unwitnessed. Verified once here rather than hardcoding the raw
    # floats at the call site.
    probe = random.Random(2)
    expected_success = probe.random() < 0.70
    expected_witnessed = probe.random() < 0.30
    assert (expected_success, expected_witnessed) == (False, False)

    world.rng = random.Random(2)
    await setup_unattended_stash(world)
    await spawn_agent(world, "thief", "market")

    result = await registry.execute(
        world, "thief", "steal", {"storage_id": "stash", "item": "gold_jewelry", "quantity": 1}
    )

    assert result.result["succeeded"] is False
    assert world.inventory.quantity("thief", "gold_jewelry") == 0
    assert world.inventory.quantity("stash", "gold_jewelry") == 5
    assert world.crime_cases[0].outcome == "failed"


@pytest.mark.asyncio
async def test_steal_blocked_when_owner_present(world, registry) -> None:
    await spawn_agent(world, "victim", "market")
    world.storages["stash"] = Storage(storage_id="stash", owner_id="victim", location_id="market")
    world.inventory.credit("stash", "gold_jewelry", 5)
    await spawn_agent(world, "thief", "market")

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "thief", "steal", {"storage_id": "stash", "item": "gold_jewelry", "quantity": 1}
        )


@pytest.mark.asyncio
async def test_steal_requires_being_at_the_storage(world, registry) -> None:
    await setup_unattended_stash(world)
    await spawn_agent(world, "thief", "far-away")

    with pytest.raises(LocationGateError):
        await registry.execute(
            world, "thief", "steal", {"storage_id": "stash", "item": "gold_jewelry", "quantity": 1}
        )


@pytest.mark.asyncio
async def test_steal_more_than_available_rejected(world, registry) -> None:
    await setup_unattended_stash(world)
    await spawn_agent(world, "thief", "market")

    with pytest.raises(InsufficientInventoryError):
        await registry.execute(
            world, "thief", "steal", {"storage_id": "stash", "item": "gold_jewelry", "quantity": 99}
        )


@pytest.mark.asyncio
async def test_steal_cannot_target_own_storage(world, registry) -> None:
    await spawn_agent(world, "owner", "market")
    world.storages["stash"] = Storage(storage_id="stash", owner_id="owner", location_id="market")
    world.inventory.credit("stash", "gold_jewelry", 5)

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "owner", "steal", {"storage_id": "stash", "item": "gold_jewelry", "quantity": 1}
        )
