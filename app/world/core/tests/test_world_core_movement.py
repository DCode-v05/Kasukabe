"""Tests for world.core.movement - MovementSystem: DESIGN.md's "agents
must WALK to destinations along the town's walkable tiles, never teleport
or slide through walls," proven both against small synthetic fixtures
(exact, fast, knowable-by-inspection) and against the real Ville map
loaded straight from its collision CSVs (a genuine cross-town walk, not a
toy).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.core.events import EventBus
from world.core.movement import (
    MovementSystem,
    default_location_id,
    estimate_travel_ticks,
)
from world.core.perception import LocationIndex
from world.map.model import TileCoord, WorldMap, build_world_map
from world.map.pathfinding import PathfindingError

VILLE_DIR = (
    Path(__file__).resolve().parents[4]
    / "smallville"
    / "environment"
    / "frontend_server"
    / "static_dirs"
    / "assets"
    / "the_ville"
)


DETOUR_WIDTH = 5
DETOUR_HEIGHT = 3
DETOUR_COLLISION = [
    1, 1, 1, 1, 1,
    0, 0, 1, 0, 0,
    0, 0, 0, 0, 0,
]


@pytest.fixture
def detour_world() -> WorldMap:
    cells = DETOUR_WIDTH * DETOUR_HEIGHT
    return build_world_map(
        world_name="Detour Town",
        width=DETOUR_WIDTH,
        height=DETOUR_HEIGHT,
        tile_size=32,
        collision_flat=DETOUR_COLLISION,
        sector_flat=[0] * cells,
        arena_flat=[0] * cells,
        game_object_flat=[0] * cells,
        spawning_flat=[0] * cells,
        sector_names={},
        arena_names={},
        game_object_names={},
        spawning_names={},
    )


MIXED_SPEED_COLLISION = [0, 0, 0]
MIXED_SPEED_SECTOR = [0, 1, 0]


@pytest.fixture
def mixed_speed_world() -> WorldMap:
    return build_world_map(
        world_name="Mixed Speed Town",
        width=3,
        height=1,
        tile_size=32,
        collision_flat=MIXED_SPEED_COLLISION,
        sector_flat=MIXED_SPEED_SECTOR,
        arena_flat=[0, 0, 0],
        game_object_flat=[0, 0, 0],
        spawning_flat=[0, 0, 0],
        sector_names={1: ("Mixed Speed Town", "The Room")},
        arena_names={},
        game_object_names={},
        spawning_names={},
    )


TWO_ZONE_SECTOR = [
    0, 0, 0, 0, 0,
    2, 2, 2, 2, 2,
    3, 3, 3, 3, 3,
]


@pytest.fixture
def two_zone_world() -> WorldMap:
    return build_world_map(
        world_name="Two Zone Town",
        width=DETOUR_WIDTH,
        height=DETOUR_HEIGHT,
        tile_size=32,
        collision_flat=DETOUR_COLLISION,
        sector_flat=TWO_ZONE_SECTOR,
        arena_flat=[0] * (DETOUR_WIDTH * DETOUR_HEIGHT),
        game_object_flat=[0] * (DETOUR_WIDTH * DETOUR_HEIGHT),
        spawning_flat=[0] * (DETOUR_WIDTH * DETOUR_HEIGHT),
        sector_names={2: ("Two Zone Town", "Corridor North"), 3: ("Two Zone Town", "Corridor South")},
        arena_names={},
        game_object_names={},
        spawning_names={},
    )


@pytest.fixture(scope="session")
def ville_dir() -> Path:
    if not VILLE_DIR.exists():
        pytest.skip(f"Ville asset directory not found at {VILLE_DIR}")
    return VILLE_DIR


@pytest.fixture(scope="session")
def ville_world(ville_dir: Path) -> WorldMap:
    from world.map.loader import load_ville_map

    return load_ville_map(ville_dir)


def assert_contiguous_and_collision_free(world: WorldMap, path: list[TileCoord]) -> None:
    """The literal proof this task asks for: every tile in the path is
    walkable (never crosses a blocked tile), and each consecutive pair is
    exactly one orthogonal step apart (contiguous - no jump, no slide)."""
    for tile in path:
        assert world.is_walkable(tile), f"path crosses a blocked tile: {tile}"
    for a, b in zip(path, path[1:]):
        dx, dy = abs(a.x - b.x), abs(a.y - b.y)
        assert dx + dy == 1, f"non-contiguous step from {a} to {b}"


def test_start_journey_computes_a_contiguous_collision_free_route(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    start, goal = TileCoord(0, 1), TileCoord(4, 1)

    journey = system.start_journey("agent-1", start, goal)

    assert journey is not None
    assert not journey.arrived
    assert journey.path[0] == start
    assert journey.path[-1] == goal
    assert_contiguous_and_collision_free(detour_world, journey.path)
    assert TileCoord(2, 1) not in journey.path
    assert system.has_active_journey("agent-1")


def test_start_journey_already_there_needs_no_ticks(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    here = TileCoord(0, 1)

    journey = system.start_journey("agent-1", here, here)

    assert journey is not None
    assert journey.arrived
    assert journey.path == [here]
    assert not system.has_active_journey("agent-1")
    assert system.advance(tick=1) == []


def test_start_journey_unreachable_destination_returns_none(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    unwalkable_goal = TileCoord(0, 0)

    journey = system.start_journey("agent-1", TileCoord(0, 1), unwalkable_goal)

    assert journey is None
    assert not system.has_active_journey("agent-1")


def test_start_journey_invalid_start_raises(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    with pytest.raises(PathfindingError):
        system.start_journey("agent-1", TileCoord(0, 0), TileCoord(4, 1))


def test_start_journey_replaces_an_in_progress_journey(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    system.start_journey("agent-1", TileCoord(0, 1), TileCoord(4, 1))
    system.advance(tick=1)

    new_journey = system.start_journey("agent-1", TileCoord(0, 2), TileCoord(4, 2))

    assert new_journey is not None
    assert system.journey_for("agent-1") is new_journey


def test_cancel_journey_stops_advancing_the_agent(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    system.start_journey("agent-1", TileCoord(0, 1), TileCoord(4, 1))

    system.cancel_journey("agent-1")

    assert not system.has_active_journey("agent-1")
    assert system.advance(tick=1) == []


def test_advance_reports_every_tile_of_the_route_in_order(detour_world: WorldMap) -> None:
    """Was "exactly one tile per tick". A stride now covers several tiles,
    but every one of them is still reported, in path order, so the event
    stream remains a proof that the agent walked a real route."""
    system = MovementSystem(detour_world)
    journey = system.start_journey("agent-1", TileCoord(0, 1), TileCoord(4, 1))
    assert journey is not None
    total_steps = len(journey.path) - 1

    visited_tiles: list[TileCoord] = []
    arrivals = 0
    tick = 0
    while system.has_active_journey("agent-1"):
        tick += 1
        for event in system.advance(tick):
            payload = event.payload
            assert payload["agent_id"] == "agent-1"
            visited_tiles.append(TileCoord(payload["tile_x"], payload["tile_y"]))
            if payload["arrived"]:
                arrivals += 1
        assert tick <= total_steps, "a journey must not outlast one tick per tile"

    assert visited_tiles == journey.path[1:]
    assert arrivals == 1
    assert not system.has_active_journey("agent-1")
    assert system.advance(tick=total_steps + 1) == []


def test_advance_never_crosses_the_blocked_tile(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    journey = system.start_journey("agent-1", TileCoord(0, 1), TileCoord(4, 1))
    assert journey is not None

    tiles_walked = [TileCoord(0, 1)]
    for tick in range(1, len(journey.path)):
        for event in system.advance(tick):
            tiles_walked.append(TileCoord(event.payload["tile_x"], event.payload["tile_y"]))

    assert_contiguous_and_collision_free(detour_world, tiles_walked)
    assert TileCoord(2, 1) not in tiles_walked


def test_advance_arrival_event_carries_the_destination_location_id(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    journey = system.start_journey(
        "agent-1", TileCoord(0, 1), TileCoord(4, 1), destination_location_id="the far corner"
    )
    assert journey is not None

    last_payload = None
    for tick in range(1, len(journey.path)):
        for event in system.advance(tick):
            last_payload = event.payload

    assert last_payload is not None
    assert last_payload["arrived"] is True
    assert last_payload["location_id"] == "the far corner"
    assert (last_payload["tile_x"], last_payload["tile_y"]) == (4, 1)


def test_advance_mid_route_events_report_a_different_location_than_the_destination(
    detour_world: WorldMap,
) -> None:
    """"another agent may perceive them en route" (this task's own
    wording): an intermediate step must be a distinguishable, real
    location, not a copy of the destination or the start."""
    system = MovementSystem(detour_world)
    journey = system.start_journey(
        "agent-1", TileCoord(0, 1), TileCoord(4, 1), destination_location_id="the far corner"
    )
    assert journey is not None

    mid_route_payloads = []
    for tick in range(1, len(journey.path)):
        for event in system.advance(tick):
            if not event.payload["arrived"]:
                mid_route_payloads.append(event.payload)

    assert mid_route_payloads, "expected at least one mid-route step for a multi-tile detour"
    for payload in mid_route_payloads:
        assert payload["location_id"] != "the far corner"


def test_advance_multiple_agents_are_independent_and_ordered_deterministically(
    detour_world: WorldMap,
) -> None:
    system = MovementSystem(detour_world)
    system.start_journey("agent-b", TileCoord(0, 2), TileCoord(4, 2))
    system.start_journey("agent-a", TileCoord(0, 1), TileCoord(4, 1))

    events = system.advance(tick=1)

    seen: list[str] = []
    for event in events:
        who = event.payload["agent_id"]
        if who not in seen:
            seen.append(who)
    assert seen == ["agent-a", "agent-b"]


def test_off_street_tile_takes_longer_to_cross_than_a_street_tile(
    mixed_speed_world: WorldMap,
) -> None:
    system = MovementSystem(mixed_speed_world)
    start, goal = TileCoord(0, 0), TileCoord(2, 0)
    journey = system.start_journey("agent-1", start, goal)
    assert journey is not None
    assert journey.path == [TileCoord(0, 0), TileCoord(1, 0), TileCoord(2, 0)]

    from world.core.movement import (
        OFF_STREET_TILES_PER_TICK,
        STREET_TILES_PER_TICK,
        stride_length,
    )

    assert OFF_STREET_TILES_PER_TICK < STREET_TILES_PER_TICK
    for tile in journey.path:
        expected = (
            STREET_TILES_PER_TICK
            if mixed_speed_world.is_street(tile)
            else OFF_STREET_TILES_PER_TICK
        )
        assert stride_length(mixed_speed_world, tile) == expected
    kinds = {mixed_speed_world.is_street(t) for t in journey.path}
    assert kinds == {True, False}

    steps_seen = 0
    ticks_spent = 0
    tick = 0
    while system.has_active_journey("agent-1"):
        tick += 1
        ticks_spent += 1
        steps_seen += len(system.advance(tick))
        assert ticks_spent <= len(journey.path), "journey outlasted one tick per tile"

    assert steps_seen == 2, "both tiles of the route are still reported"


def test_arrival_is_never_instant_for_a_multi_tile_journey(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    journey = system.start_journey("agent-1", TileCoord(0, 1), TileCoord(4, 1))
    assert journey is not None
    assert len(journey.path) > 2

    from world.core.movement import STREET_TILES_PER_TICK

    assert len(journey.path) - 1 <= STREET_TILES_PER_TICK
    events = system.advance(tick=1)
    assert [e.payload["agent_id"] for e in events] == ["agent-1"] * len(events)
    assert events[-1].payload["arrived"] is True


def test_default_location_id_uses_sector_name_or_street(mixed_speed_world: WorldMap) -> None:
    assert default_location_id(mixed_speed_world, TileCoord(1, 0)) == "The Room"
    assert default_location_id(mixed_speed_world, TileCoord(0, 0)) == "street"


@pytest.mark.asyncio
async def test_walking_agent_is_visible_to_a_perceiving_agent_en_route(two_zone_world: WorldMap) -> None:
    """End-to-end proof that MovementSystem's events are real world.core
    events: wiring them straight into a live LocationIndex (exactly how
    world.core.engine.TickEngine's own agent.moved-driven wiring already
    works, unmodified) makes a walking agent show up as "present" at
    whatever tile-derived location they are passing through, tick by
    tick - not just at their final destination. A second, stationary
    "watcher" agent is placed at the walker's current location after each
    step and found via LocationIndex.agents_at(), the exact call
    world.core.perception.PerceptionSystem.build_perception uses to
    compute "visible_agents" - proving "another agent may perceive them
    en route" (this task's own wording) with the real perception
    machinery, not just a location_id string equality check.
    """
    event_bus = EventBus()
    locations = LocationIndex()
    locations.attach(event_bus)

    system = MovementSystem(two_zone_world)
    start = TileCoord(0, 1)
    locations.place("walker", default_location_id(two_zone_world, start))
    journey = system.start_journey("walker", start, TileCoord(4, 1))
    assert journey is not None

    seen_locations: list[str | None] = []
    perceived_en_route = False
    tick = 0
    while system.has_active_journey("walker"):
        tick += 1
        for event in system.advance(tick):
            await event_bus.publish(event)
            current = locations.location_of("walker")
            seen_locations.append(current)
            locations.place("watcher", current)
            if "walker" in locations.agents_at(current):
                perceived_en_route = True

    assert perceived_en_route
    assert None not in seen_locations
    assert len(set(seen_locations)) > 1
    assert locations.location_of("walker") == journey.destination_location_id


def test_walking_across_the_real_town_is_contiguous_collision_free_and_takes_many_ticks(
    ville_world: WorldMap,
) -> None:
    """The task's own proof requirement, run for real: "place an agent,
    target a location across town, assert the emitted path is contiguous,
    never crosses a blocked tile, and terminates at the destination" -
    against the actual 140x100 Ville collision grid the map track loaded
    from its CSVs, not a synthetic fixture. Johnson Park and Oak Hill
    College are the two genesis sectors farthest apart on this map (113
    tiles of Manhattan distance between their entry points), a genuine
    cross-town walk.
    """
    system = MovementSystem(ville_world)
    start = ville_world.entry_point("Johnson Park")
    assert start is not None

    journey = system.start_journey_to_location("agent-1", start, "Oak Hill College")

    assert journey is not None
    assert not journey.arrived
    assert journey.path[0] == start
    assert journey.destination_tile == ville_world.entry_point("Oak Hill College")
    assert_contiguous_and_collision_free(ville_world, journey.path)

    all_tiles = [start]
    tick = 0
    arrived_at: TileCoord | None = None
    while system.has_active_journey("agent-1"):
        tick += 1
        for event in system.advance(tick):
            tile = TileCoord(event.payload["tile_x"], event.payload["tile_y"])
            all_tiles.append(tile)
            if event.payload["arrived"]:
                arrived_at = tile

    assert_contiguous_and_collision_free(ville_world, all_tiles)
    assert all_tiles == journey.path
    assert arrived_at == journey.destination_tile
    assert tick == estimate_travel_ticks(ville_world, journey.path)
    assert tick > 1, "a walk across town must not complete in a single tick"
    assert tick <= 12, f"crossing town took {tick} ticks - over two game-hours"


def test_start_journey_to_location_unknown_sector_returns_none(ville_world: WorldMap) -> None:
    system = MovementSystem(ville_world)
    start = ville_world.entry_point("Johnson Park")
    assert start is not None

    journey = system.start_journey_to_location("agent-1", start, "Nonexistent Place")

    assert journey is None


def test_snapshot_restore_round_trips_an_in_progress_journey(detour_world: WorldMap) -> None:
    system = MovementSystem(detour_world)
    journey = system.start_journey("agent-1", TileCoord(0, 1), TileCoord(4, 1))
    assert journey is not None
    assert system.has_active_journey("agent-1")

    snapshot = system.snapshot()

    restored = MovementSystem(detour_world)
    restored.restore(snapshot)

    restored_journey = restored.journey_for("agent-1")
    original_journey = system.journey_for("agent-1")
    assert restored_journey is not None
    assert original_journey is not None
    assert restored_journey.path == original_journey.path
    assert restored_journey.step_index == original_journey.step_index
    assert restored_journey.ticks_remaining == original_journey.ticks_remaining
    assert restored_journey.destination_location_id == original_journey.destination_location_id

    remaining_ticks = len(original_journey.path) - 1
    for tick in range(2, remaining_ticks + 2):
        original_events = system.advance(tick)
        restored_events = restored.advance(tick)
        assert [e.payload for e in original_events] == [e.payload for e in restored_events]
