from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError
from world.tools.locations import LOCATION_FOREST, farmland_location, ore_outcrop_location
from world.tools.primitives.resources import regenerate_zones
from world.tools.tests.conftest import spawn_agent


@pytest.mark.asyncio
async def test_harvest_credits_inventory_and_depletes_zone(world, registry) -> None:
    await spawn_agent(world, "farmer", farmland_location("wheat"))
    zone_before = world.resource_zones[farmland_location("wheat")].remaining_yield

    result = await registry.execute(world, "farmer", "harvest", {"crop": "wheat", "quantity": 5})

    assert result.result == {"item": "wheat", "quantity": 5}
    assert world.inventory.quantity("farmer", "wheat") == 5
    assert world.resource_zones[farmland_location("wheat")].remaining_yield == zone_before - 5


@pytest.mark.asyncio
async def test_harvest_wrong_location_is_gated(world, registry) -> None:
    await spawn_agent(world, "farmer", farmland_location("rice"))
    with pytest.raises(LocationGateError):
        await registry.execute(world, "farmer", "harvest", {"crop": "wheat", "quantity": 1})


@pytest.mark.asyncio
async def test_harvest_unknown_crop_rejected(world, registry) -> None:
    await spawn_agent(world, "farmer", farmland_location("wheat"))
    with pytest.raises(Exception):
        await registry.execute(world, "farmer", "harvest", {"crop": "not_a_crop", "quantity": 1})


@pytest.mark.asyncio
async def test_mine_credits_ore(world, registry) -> None:
    await spawn_agent(world, "miner", ore_outcrop_location("gold"))
    result = await registry.execute(world, "miner", "mine", {"metal": "gold", "quantity": 2})
    assert result.result == {"item": "gold_ore", "quantity": 2}
    assert world.inventory.quantity("miner", "gold_ore") == 2


@pytest.mark.asyncio
async def test_fell_timber_never_depletes_forest(world, registry) -> None:
    await spawn_agent(world, "lumberjack", LOCATION_FOREST)
    zone = world.resource_zones[LOCATION_FOREST]
    assert zone.remaining_yield is None

    await registry.execute(world, "lumberjack", "fell_timber", {"quantity": 500})

    assert world.resource_zones[LOCATION_FOREST].remaining_yield is None
    assert world.inventory.quantity("lumberjack", "timber") == 500


def test_regenerate_zones_caps_at_default_yield(world) -> None:
    location_id = farmland_location("corn")
    world.resource_zones[location_id].remaining_yield = 9_999
    regenerate_zones(world)
    assert world.resource_zones[location_id].remaining_yield == 10_000
