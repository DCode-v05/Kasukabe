"""Unit tests for world.map.pathfinding.find_path (A*) against the tiny
synthetic fixture (see conftest.py): a one-room house with a single door
onto an exterior street, so reachability has one obviously correct answer.
"""

from __future__ import annotations

import pytest

from world.map.model import TileCoord, WorldMap, build_world_map
from world.map.pathfinding import PathfindingError, find_path, manhattan_distance


def assert_valid_path(world: WorldMap, path: list[TileCoord], start: TileCoord, goal: TileCoord, *, allow_diagonal: bool = False) -> None:
    assert path[0] == start
    assert path[-1] == goal
    for tile in path:
        assert world.is_walkable(tile)
    for a, b in zip(path, path[1:]):
        dx, dy = abs(a.x - b.x), abs(a.y - b.y)
        if allow_diagonal:
            assert max(dx, dy) == 1
        else:
            assert dx + dy == 1


def test_manhattan_distance() -> None:
    assert manhattan_distance(TileCoord(0, 0), TileCoord(3, 4)) == 7


def test_start_equals_goal_returns_single_tile_path(tiny_world: WorldMap) -> None:
    start = TileCoord(2, 4)
    assert find_path(tiny_world, start, start) == [start]


def test_path_from_street_into_the_house_goes_through_the_door(tiny_world: WorldMap) -> None:
    start = TileCoord(0, 4)
    goal = TileCoord(1, 1)
    path = find_path(tiny_world, start, goal)
    assert path is not None
    assert_valid_path(tiny_world, path, start, goal)
    assert TileCoord(2, 3) in path


def test_path_within_the_room(tiny_world: WorldMap) -> None:
    start = TileCoord(1, 1)
    goal = TileCoord(3, 2)
    path = find_path(tiny_world, start, goal)
    assert path is not None
    assert_valid_path(tiny_world, path, start, goal)


def test_unwalkable_goal_returns_none_not_an_error(tiny_world: WorldMap) -> None:
    assert find_path(tiny_world, TileCoord(0, 4), TileCoord(0, 0)) is None


def test_unwalkable_start_raises(tiny_world: WorldMap) -> None:
    with pytest.raises(PathfindingError):
        find_path(tiny_world, TileCoord(0, 0), TileCoord(2, 2))


def test_out_of_bounds_start_raises(tiny_world: WorldMap) -> None:
    with pytest.raises(PathfindingError):
        find_path(tiny_world, TileCoord(99, 99), TileCoord(2, 2))


def test_out_of_bounds_goal_raises(tiny_world: WorldMap) -> None:
    with pytest.raises(PathfindingError):
        find_path(tiny_world, TileCoord(2, 2), TileCoord(99, 99))


def test_diagonal_path_still_reaches_the_goal(tiny_world: WorldMap) -> None:
    start = TileCoord(0, 4)
    goal = TileCoord(3, 1)
    path = find_path(tiny_world, start, goal, allow_diagonal=True)
    assert path is not None
    assert_valid_path(tiny_world, path, start, goal, allow_diagonal=True)


def test_path_is_shortest_length(tiny_world: WorldMap) -> None:
    path = find_path(tiny_world, TileCoord(0, 4), TileCoord(2, 2))
    assert path is not None
    assert len(path) == 5


STREET_CHOICE_WIDTH = 6
STREET_CHOICE_HEIGHT = 3
STREET_CHOICE_COLLISION = [
    0, 0, 0, 0, 0, 0,
    0, 1, 1, 1, 1, 0,
    0, 0, 0, 0, 0, 0,
]
STREET_CHOICE_SECTOR = [
    0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0,
    1, 1, 1, 1, 1, 1,
]


@pytest.fixture
def street_choice_world() -> WorldMap:
    return build_world_map(
        world_name="Street Choice Town",
        width=STREET_CHOICE_WIDTH,
        height=STREET_CHOICE_HEIGHT,
        tile_size=32,
        collision_flat=STREET_CHOICE_COLLISION,
        sector_flat=STREET_CHOICE_SECTOR,
        arena_flat=[0] * (STREET_CHOICE_WIDTH * STREET_CHOICE_HEIGHT),
        game_object_flat=[0] * (STREET_CHOICE_WIDTH * STREET_CHOICE_HEIGHT),
        spawning_flat=[0] * (STREET_CHOICE_WIDTH * STREET_CHOICE_HEIGHT),
        sector_names={1: ("Street Choice Town", "The Room")},
        arena_names={},
        game_object_names={},
        spawning_names={},
    )


def test_prefer_streets_picks_the_street_route_over_an_equally_long_room_route(
    street_choice_world: WorldMap,
) -> None:
    start = TileCoord(0, 1)
    goal = TileCoord(5, 1)
    path = find_path(street_choice_world, start, goal, prefer_streets=True)
    assert path is not None
    assert len(path) == 8
    assert_valid_path(street_choice_world, path, start, goal)
    detour = path[1:-1]
    assert all(tile.y == 0 for tile in detour), path
    assert TileCoord(2, 2) not in path
    assert sum(1 for tile in path if street_choice_world.is_street(tile)) == len(path)


def test_prefer_streets_false_still_finds_an_equally_short_path(
    street_choice_world: WorldMap,
) -> None:
    start = TileCoord(0, 1)
    goal = TileCoord(5, 1)
    path = find_path(street_choice_world, start, goal, prefer_streets=False)
    assert path is not None
    assert len(path) == 8
    assert_valid_path(street_choice_world, path, start, goal)
