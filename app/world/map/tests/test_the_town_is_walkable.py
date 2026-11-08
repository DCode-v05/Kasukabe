"""§3.1-§3.3 — a town you cannot walk across is not a town.

Run 15's residents diagnosed this themselves, in dialogue, before any
instrument did:

    Corwin Thorne: "I tried. The moves aren't sticking - kept ending up
    back at Hobbs, or wandering street tiles that don't lead anywhere."

    Liora Thorne: "Five hours walking, never reaching the College."

Measured afterwards: 107 "no walkable route" refusals in one run, to the
town's busiest places - Kasukabe Farmland 55, Willows Market 51, Hobbs
Cafe 28, Harvey Oak 26. Of the 103 journeys that did start, the median
was 68 ticks and the longest 179. A tick is ten game-minutes, so the
median walk across this town was 11 game-hours.

Two separate faults, and this file pins both:

  1. The map is DISCONNECTED. 89% of tiles are walkable but only 26% of
     them are reachable on foot from Hobbs Cafe. The genesis expansion
     (farmland, forest, ore outcrop, temple, government) is appended as a
     strip on the south edge and no walkable seam joins it to the town,
     so every workplace the economy depends on is on an island.

  2. Walking is CALIBRATED WRONG. One tick per street tile at ten
     game-minutes a tick means a resident covers one tile per ten
     minutes. Crossing the town is then a full working day.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from ville_assets import require_ville_assets
from world.core.movement import estimate_travel_ticks
from world.map.model import TileCoord
from world.tools.bootstrap import build_world
from world.tools.primitives.places import known_places

MAX_CROSSING_TICKS = 12
MAX_CROSSING_GAME_HOURS = 2


@pytest_asyncio.fixture(scope="module")
async def town():
    """GROW-27: skip, do not fail, when the Ville tileset is not mounted.

    Without it `build_world` logs a warning and leaves
    `WorldState.movement` as None, and every test below then died on
    `NoneType has no attribute world_map` - five hard failures that look
    exactly like a broken map and are nothing of the kind. The other 47
    asset-dependent tests in this package already skipped; these did not,
    so the same absent directory produced two different verdicts.

    The gate also records what went unproven, so the run's summary says
    the town's walkability was NOT checked instead of leaving five quiet
    's' characters to carry that news.
    """
    require_ville_assets(
        "the town is one connected walkable place and can be crossed in "
        "under two game-hours (run 15: 107 'no walkable route' refusals)"
    )
    world, _registry = await build_world(genesis_tick=0, seed=1)
    assert world.movement is not None and world.movement.world_map is not None, (
        "the Ville assets are present but build_world still produced no map - "
        "that is a real failure, not a missing mount"
    )
    return world


def _entry_points(world) -> dict:
    world_map = world.movement.world_map
    out = {}
    for place in known_places(world):
        point = world_map.entry_point(place["place_id"])
        if point is not None:
            out[place["place_id"]] = point
    return out


def _walkable_region(world_map, start: TileCoord) -> set[tuple[int, int]]:
    """Every tile reachable on foot from `start`. Plain flood fill, so a
    failure here is about the map and not about the pathfinder."""
    seen = {(start.x, start.y)}
    stack = [(start.x, start.y)]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (nx, ny) in seen or not world_map.in_bounds_xy(nx, ny):
                continue
            if world_map.is_walkable(TileCoord(x=nx, y=ny)):
                seen.add((nx, ny))
                stack.append((nx, ny))
    return seen


@pytest.mark.asyncio
async def test_the_map_is_one_connected_place(town):
    """Every place a resident may stand is on the same walkable landmass.

    Flood fill, not pathfinding: if this fails, no amount of routing work
    will help, because there is no route to find.
    """
    world_map = town.movement.world_map
    points = _entry_points(town)
    assert points, "no place has a map entry point - the fixture is wrong"

    region = _walkable_region(world_map, points["Hobbs Cafe"])
    stranded = {
        name: point for name, point in points.items()
        if (point.x, point.y) not in region
    }
    assert not stranded, (
        "these places cannot be walked to from the town centre: "
        + ", ".join(f"{n} at {p}" for n, p in sorted(stranded.items()))
    )


@pytest.mark.asyncio
async def test_the_workplaces_specifically_are_reachable(town):
    """Named, because these six were the stranded ones and because every
    trade in the town depends on them."""
    world_map = town.movement.world_map
    points = _entry_points(town)
    region = _walkable_region(world_map, points["Hobbs Cafe"])
    for place in ("Kasukabe Farmland", "Kasukabe Forest", "Kasukabe Ore Outcrop",
                  "Kasukabe Temple", "Kasukabe Government Complex",
                  "The Willows Market and Pharmacy"):
        point = points[place]
        assert (point.x, point.y) in region, f"{place} is on an island"


@pytest.mark.asyncio
async def test_every_pair_of_places_has_a_route(town):
    """§3.1 in full: pairwise, not a sample. The pair that broke this was
    Kasukabe Farmland to The Willows Market, and no sampled test would
    reliably have drawn it."""
    movement = town.movement
    points = _entry_points(town)
    names = sorted(points)
    missing = []
    for origin in names:
        for destination in names:
            if origin == destination:
                continue
            journey = movement.start_journey_to_location(
                "probe", points[origin], destination
            )
            if journey is None:
                missing.append(f"{origin} -> {destination}")
    assert not missing, (
        f"{len(missing)} of {len(names) * (len(names) - 1)} routes do not exist. "
        f"First few: {missing[:6]}"
    )


@pytest.mark.asyncio
async def test_crossing_the_town_fits_inside_a_working_day(town):
    """§3.3. A budget agreed before measuring: two game-hours, twelve
    ticks. The run that prompted this had a median of 68 and a worst of
    179 - a day and a quarter of continuous walking."""
    movement = town.movement
    world_map = movement.world_map
    points = _entry_points(town)
    names = sorted(points)

    worst = []
    for origin in names:
        for destination in names:
            if origin == destination:
                continue
            journey = movement.start_journey_to_location(
                "probe", points[origin], destination
            )
            if journey is None:
                continue
            ticks = estimate_travel_ticks(world_map, journey.path)
            if ticks > MAX_CROSSING_TICKS:
                worst.append((ticks, f"{origin} -> {destination}"))

    worst.sort(reverse=True)
    assert not worst, (
        f"{len(worst)} routes take longer than {MAX_CROSSING_TICKS} ticks "
        f"({MAX_CROSSING_GAME_HOURS} game-hours). Worst: "
        + "; ".join(f"{t} ticks {r}" for t, r in worst[:5])
    )


@pytest.mark.asyncio
async def test_a_walk_is_still_something_you_can_watch(town):
    """The opposite failure. Travel must not become teleportation: another
    resident has to be able to see a traveller partway, which is what
    every step publishing agent.moved is for."""
    movement = town.movement
    points = _entry_points(town)
    journey = movement.start_journey_to_location(
        "probe", points["Hobbs Cafe"], "Kasukabe Farmland"
    )
    assert journey is not None
    assert not journey.arrived, "a cross-town walk must take at least one tick"
    assert len(journey.path) > 1
