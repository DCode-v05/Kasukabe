"""Tests for world.tools.primitives.movement.move_handler - task
"walking": DESIGN.md's "agents must WALK to destinations along the
town's walkable tiles, never teleport", proven through the ACTUAL seam a
live agent uses (ToolRegistry.execute("move", ...)), not by calling
world.core.movement.MovementSystem directly (that module's own 18 tests
already cover it in isolation - see world/core/tests/test_world_core_
movement.py). What these tests prove is the WIRING: that move_handler
really does start a MovementSystem route instead of publishing one
instant agent.moved, that a tick loop driving MovementSystem.advance()
and publishing its events (exactly what world.core.engine.TickEngine.tick
now does once a caller wires movement_system= into it - see that
module's docstring) really does carry the walk to completion through the
real WorldState/LocationIndex/EventBus wiring, and that every other
tool/test in this codebase that still depends on opaque, non-map
location_id strings keeps working unchanged (movement.py primitive's own
module docstring explains why that fallback exists).

The `world`/`registry` fixtures (conftest.py) build a real WorldState via
bootstrap.build_world() with NO world_map override, so they load
Kasukabe's actual genesis map from disk (present in this repo and
mounted read-only by docker-compose.yml's "test" service) - `world.
movement` is a real MovementSystem over real town geometry, not a
stub. "Johnson Park" and "Oak Hill College" are reused throughout as the
two genesis sectors farthest apart on that map (113 tiles of Manhattan
distance between their entry points - confirmed by world/core/tests/
test_world_core_movement.py's own real-map test), for a genuine
cross-town walk that cannot pass by accident the way a one-tile hop
could.
"""

from __future__ import annotations

import pytest

import world.core.movement as movement_module

from world.map.model import TileCoord, WorldMap, build_world_map
from world.tools.bootstrap import build_world
from world.tools.tests.conftest import spawn_agent
from world.tools.tests.conftest import needs_the_map

pytestmark = needs_the_map


def assert_contiguous_and_collision_free(world_map: WorldMap, path: list[TileCoord]) -> None:
    for tile in path:
        assert world_map.is_walkable(tile), f"path crosses a blocked tile: {tile}"
    for a, b in zip(path, path[1:]):
        dx, dy = abs(a.x - b.x), abs(a.y - b.y)
        assert dx + dy == 1, f"non-contiguous step from {a} to {b}"


async def drive_journey_to_completion(world, agent_id: str) -> int:
    """Exactly what a tick loop is expected to do (world.core.engine.
    TickEngine.tick's own new movement wiring - see that module's
    docstring): advance() once per tick, publish every returned event on
    the SAME EventBus WorldState already uses, until nothing is left to
    advance. Returns the number of ticks it took."""
    tick = 0
    while world.movement.has_active_journey(agent_id):
        tick += 1
        for event in world.movement.advance(tick):
            await world.event_bus.publish(event)
    return tick


@pytest.mark.asyncio
async def test_move_updates_location_index(world, registry) -> None:
    """A `move` call to a real map sector starts a route instead of
    jumping there instantly: the location index still reports the
    agent's ORIGINAL location the instant the tool call returns (not one
    tick has actually elapsed), and only reflects the destination once a
    tick loop has actually walked the whole route."""
    assert world.movement is not None, "build_world() should have loaded the real Kasukabe map"
    await spawn_agent(world, "a1", "Johnson Park")
    assert world.location_of("a1") == "Johnson Park"

    result = await registry.execute(world, "a1", "move", {"destination_location_id": "Oak Hill College"})

    assert result.result["walking"] is True
    assert world.location_of("a1") == "Johnson Park"
    assert world.movement.has_active_journey("a1")
    assert result.events == []

    tick = await drive_journey_to_completion(world, "a1")

    assert world.location_of("a1") == "Oak Hill College"
    assert 1 < tick <= 12, f"cross-town walk took {tick} ticks"


@pytest.mark.asyncio
async def test_move_has_no_location_gate(world, registry) -> None:
    await spawn_agent(world, "a1", "forest")
    result = await registry.execute(world, "a1", "move", {"destination_location_id": "temple"})
    assert result.result["location_id"] == "temple"
    assert result.result["walked"] is False


@pytest.mark.asyncio
async def test_move_rejects_empty_destination(world, registry) -> None:
    from world.tools.errors import ToolValidationError

    await spawn_agent(world, "a1", "park")
    with pytest.raises(ToolValidationError):
        await registry.execute(world, "a1", "move", {"destination_location_id": ""})


@pytest.mark.asyncio
async def test_move_through_registry_walks_across_town_contiguous_and_collision_free(
    world, registry
) -> None:
    """The task's own proof requirement: "an agent crossing town takes
    many ticks, the path is contiguous, never crosses a blocked tile, and
    its reported location matches its tile at every step" - run through
    registry.execute("move", ...), the exact call a live agent's engine
    process makes (run_genesis.py's /agents/{agent_id}/tool_calls route),
    not MovementSystem directly.
    """
    assert world.movement is not None
    world_map = world.movement.world_map
    await spawn_agent(world, "walker", "Johnson Park")

    result = await registry.execute(world, "walker", "move", {"destination_location_id": "Oak Hill College"})
    assert result.result["walking"] is True

    journey = world.movement.journey_for("walker")
    assert journey is not None
    assert len(journey.path) > 50
    assert_contiguous_and_collision_free(world_map, journey.path)

    tiles_visited: list[TileCoord] = [journey.path[0]]
    tick = 0
    while world.movement.has_active_journey("walker"):
        tick += 1
        for event in world.movement.advance(tick):
            await world.event_bus.publish(event)
            step_tile = TileCoord(event.payload["tile_x"], event.payload["tile_y"])
            tiles_visited.append(step_tile)
            assert world.location_of("walker") == event.payload["location_id"]
            assert world_map.is_walkable(step_tile)

    assert tiles_visited == journey.path
    assert 1 < tick <= 12, f"cross-town walk took {tick} ticks"
    assert world.location_of("walker") == "Oak Hill College"
    assert world.agent_tiles["walker"] == (journey.path[-1].x, journey.path[-1].y)


@pytest.mark.asyncio
async def test_move_already_at_destination_is_zero_tick_but_still_real(world, registry) -> None:
    """start == destination tile: MovementSystem's own "already there"
    contract (zero ticks, nothing registered in movement.journeys) - but
    the resulting agent.moved still carries real tile_x/tile_y and
    arrived=True, not the opaque pre-walking shape."""
    assert world.movement is not None
    await spawn_agent(world, "a1", "Johnson Park")

    result = await registry.execute(world, "a1", "move", {"destination_location_id": "Johnson Park"})

    assert result.result["location_id"] == "Johnson Park"
    assert result.result["walking"] is False
    assert result.result["walked"] is True
    assert len(result.events) == 1
    payload = result.events[0].payload
    assert payload["arrived"] is True
    assert isinstance(payload["tile_x"], int) and isinstance(payload["tile_y"], int)
    assert not world.movement.has_active_journey("a1")
    assert world.location_of("a1") == "Johnson Park"


@pytest.mark.asyncio
async def test_move_falls_back_to_opaque_jump_when_destination_is_not_a_real_map_location(
    world, registry
) -> None:
    """"mill"/"kitchen"/business-slug locations (world.tools.locations.py's
    own opaque constants, and every genesis placeholder like
    "home:{household_id}") are not sectors on the real map yet - `move`
    must keep publishing the pre-walking instant jump for these,
    unchanged, so every other primitive/test still depending on that
    contract (world/tools/tests/test_bootstrap_integration.py's farm-to-
    table chain, for one) does not regress."""
    assert world.movement is not None
    await spawn_agent(world, "a1", "home:hh-1")

    result = await registry.execute(world, "a1", "move", {"destination_location_id": "mill"})

    assert result.result["location_id"] == "mill"
    assert result.result["walked"] is False, (
        "TOOL-05: a jump that looks identical to a walk is the defect"
    )
    assert "no route on the map" in result.result["note"]
    assert len(result.events) == 1
    payload = result.events[0].payload
    assert payload["agent_id"] == "a1"
    assert payload["location_id"] == "mill"
    assert payload["target_agent_id"] == "a1"
    assert payload["walked"] is False
    assert not world.movement.has_active_journey("a1")
    assert world.location_of("a1") == "mill"


TINY_WIDTH = 5
TINY_HEIGHT = 3
TINY_COLLISION = [
    1, 1, 1, 1, 1,
    0, 0, 1, 0, 0,
    0, 0, 0, 0, 0,
]
TINY_SECTOR = [
    0, 0, 0, 0, 0,
    1, 0, 0, 0, 2,
    0, 0, 0, 0, 0,
]


def build_tiny_world() -> WorldMap:
    cells = TINY_WIDTH * TINY_HEIGHT
    return build_world_map(
        world_name="Tiny Town",
        width=TINY_WIDTH,
        height=TINY_HEIGHT,
        tile_size=32,
        collision_flat=TINY_COLLISION,
        sector_flat=TINY_SECTOR,
        arena_flat=[0] * cells,
        game_object_flat=[0] * cells,
        spawning_flat=[0] * cells,
        sector_names={1: ("Tiny Town", "Start Room"), 2: ("Tiny Town", "Dest Room")},
        arena_names={},
        game_object_names={},
        spawning_names={},
    )


@pytest.fixture
def one_tile_strides(monkeypatch):
    """Make one tick cross one tile, for tests about being PARTWAY along a
    route.

    A resident normally crosses a stride of 40 street tiles per tick, which
    is what makes crossing the real 140x130 town a four-tick walk instead of
    a 404-tick one. These two tests need an agent stopped mid-route, and on
    a five-tile fixture map a full stride finishes the journey in one tick -
    there is no midpoint to stand on. The behaviour under test (resume from
    where you actually are, not from where you set off) has nothing to do
    with how far a stride reaches, so the stride is pinned rather than the
    scenario contorted.
    """
    monkeypatch.setattr(movement_module, "STREET_TILES_PER_TICK", 1)
    monkeypatch.setattr(movement_module, "OFF_STREET_TILES_PER_TICK", 1)


@pytest.mark.asyncio
async def test_move_again_mid_journey_resumes_from_the_current_tile_not_the_original_start(one_tile_strides) -> None:
    """resolve_current_tile's first branch: an in-progress journey's
    current tile, not the agent's ORIGINAL spawn tile - MovementSystem.
    start_journey's own "a fresh destination simply is a fresh journey"
    contract, exercised through move_handler twice in a row."""
    world, registry = await build_world(seed=1, world_map=build_tiny_world())
    await spawn_agent(world, "a1", "Start Room")

    await registry.execute(world, "a1", "move", {"destination_location_id": "Dest Room"})
    tick = 0
    tick += 1
    for event in world.movement.advance(tick):
        await world.event_bus.publish(event)
    mid_journey = world.movement.journey_for("a1")
    assert mid_journey is not None
    current_tile = mid_journey.current_tile
    assert current_tile != TileCoord(0, 1)

    await registry.execute(world, "a1", "move", {"destination_location_id": "Start Room"})

    new_journey = world.movement.journey_for("a1")
    assert new_journey is not None
    assert new_journey.path[0] == current_tile


@pytest.mark.asyncio
async def test_move_resumes_from_last_recorded_tile_after_a_cancelled_journey(one_tile_strides) -> None:
    """resolve_current_tile's second branch: world.agent_tiles (recorded
    by bootstrap.attach_tile_tracking from a real advance() step) is what
    lets a `move` call resume correctly even with NO active journey and a
    LocationIndex string ("street") that cannot resolve back to a tile on
    its own - entry_point has no sector named "street" to look up."""
    world, registry = await build_world(seed=1, world_map=build_tiny_world())
    await spawn_agent(world, "a1", "Start Room")

    await registry.execute(world, "a1", "move", {"destination_location_id": "Dest Room"})
    for event in world.movement.advance(1):
        await world.event_bus.publish(event)
    assert world.location_of("a1") == "street"
    recorded_tile = TileCoord(*world.agent_tiles["a1"])

    world.movement.cancel_journey("a1")
    assert not world.movement.has_active_journey("a1")

    await registry.execute(world, "a1", "move", {"destination_location_id": "Dest Room"})

    resumed_journey = world.movement.journey_for("a1")
    assert resumed_journey is not None
    assert resumed_journey.path[0] == recorded_tile


DISCONNECTED_WIDTH = 5
DISCONNECTED_HEIGHT = 1
DISCONNECTED_COLLISION = [0, 0, 1, 0, 0]
DISCONNECTED_SECTOR = [1, 1, 0, 2, 2]


def build_disconnected_world() -> WorldMap:
    cells = DISCONNECTED_WIDTH * DISCONNECTED_HEIGHT
    return build_world_map(
        world_name="Disconnected Town",
        width=DISCONNECTED_WIDTH,
        height=DISCONNECTED_HEIGHT,
        tile_size=32,
        collision_flat=DISCONNECTED_COLLISION,
        sector_flat=DISCONNECTED_SECTOR,
        arena_flat=[0] * cells,
        game_object_flat=[0] * cells,
        spawning_flat=[0] * cells,
        sector_names={1: ("Disconnected Town", "Left Room"), 2: ("Disconnected Town", "Right Room")},
        arena_names={},
        game_object_names={},
        spawning_names={},
    )


@pytest.mark.asyncio
async def test_move_to_a_real_but_unreachable_destination_does_not_teleport() -> None:
    world, registry = await build_world(seed=1, world_map=build_disconnected_world())
    await spawn_agent(world, "a1", "Left Room")

    result = await registry.execute(world, "a1", "move", {"destination_location_id": "Right Room"})

    assert result.result["walking"] is False
    assert "error" in result.result
    assert result.events == []
    assert not world.movement.has_active_journey("a1")
    assert world.location_of("a1") == "Left Room"


@pytest.mark.asyncio
async def test_moving_again_to_the_same_place_does_not_restart_the_walk(one_tile_strides) -> None:
    """An agent that re-issues `move` to where it is already walking must
    be told it is already on the way, not silently set back to the start.

    Uses one-tile strides so the walk is still in progress on the second
    call - at the town's real speed this five-tile fixture route finishes
    inside a single stride, and there would be no journey to interrupt.

    Caught by the §4.1 eval on a live run: of 13 journeys started, 9 were
    never seen to finish, and six of those were one resident - Marta
    Thorne - issuing `move` to the Kasukabe Government Complex five times
    and the Temple once, all inside a single turn. Every call restarted the
    journey, so she walked the first leg over and over and arrived nowhere.

    This is the mechanism behind what the residents described in run 15:
    "kept ending up back at Hobbs", "the moves aren't sticking".
    """
    world, registry = await build_world(seed=1, world_map=build_tiny_world())
    await spawn_agent(world, "a1", "Start Room")

    await registry.execute(
        world, "a1", "move", {"destination_location_id": "Dest Room"}
    )
    journey = world.movement.journey_for("a1")
    assert journey is not None
    progress_before = journey.step_index

    for event in world.movement.advance(1):
        await world.event_bus.publish(event)

    again = await registry.execute(
        world, "a1", "move", {"destination_location_id": "Dest Room"}
    )

    still = world.movement.journey_for("a1")
    if still is not None:
        assert still.step_index >= progress_before, (
            "re-issuing the same move threw away the walking already done"
        )
    assert "already" in str(again.result).lower(), (
        f"result gave the agent no reason to stop repeating itself: {again.result}"
    )
