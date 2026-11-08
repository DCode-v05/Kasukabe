"""A* pathfinding over a WorldMap's walkability grid.

Pure computation (no I/O, no world mutation) - app/CONVENTIONS.md's carve
out for "tile geometry" applies directly, so find_path is a plain
synchronous function. world.core.movement.MovementSystem (DESIGN.md
section 2's `move` primitive, wired up one layer above this module) calls
this once per move request and then consumes the returned tile list step
by step, tick by tick; that consumption does not belong in this module
(this module has no notion of ticks or agents, only geometry).

Street preference (prefer_streets, default on): DESIGN.md's agent-pathing
requirement is "prefer roads and paths where they exist", not just "avoid
walls". WorldMap.is_street(tile) (a walkable tile that belongs to no
sector - public ground between buildings, world/map/model.py's own
definition) is the town's road network in exactly the sense DESIGN.md
means: a route that hugs the street is weighted cheaper per tile than one
that cuts through a building interior or open sector ground, so A* picks
the street-hugging route whenever one exists of comparable length, not
just whenever it happens to also be strictly shortest. OFF_STREET_COST_
MULTIPLIER keeps the minimum possible per-step cost at STREET_STEP_COST's
value (1 for an orthogonal street step), which is exactly what
manhattan_distance already assumes, so the heuristic stays admissible
(never overestimates true remaining cost) regardless of the multiplier's
size - see find_path's docstring for the full argument.
"""

from __future__ import annotations

import heapq
import math

from world.map.model import TileCoord, WorldMap
from world.map.walkability import in_bounds_xy, neighbors4, neighbors8

STREET_STEP_COST = 1
OFF_STREET_COST_MULTIPLIER = 2


class PathfindingError(ValueError):
    """Raised for a request that can never produce a path regardless of
    map layout (out-of-bounds or unwalkable start) - distinct from
    find_path simply returning None for "no route exists between two
    otherwise-valid tiles", which is an ordinary, expected outcome, not
    an error."""


def manhattan_distance(a: TileCoord, b: TileCoord) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def reconstruct_path(came_from: dict[TileCoord, TileCoord], end: TileCoord) -> list[TileCoord]:
    path = [end]
    while path[-1] in came_from:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def find_path(
    world: WorldMap,
    start: TileCoord,
    goal: TileCoord,
    *,
    allow_diagonal: bool = False,
    prefer_streets: bool = True,
) -> list[TileCoord] | None:
    """Cheapest walkable path from start to goal, inclusive of both ends.

    Returns None when start and goal are both valid, walkable tiles but
    no route connects them (goal is behind a sealed wall, say) - this is
    the normal "unreachable" outcome, not an error. Raises
    PathfindingError for a request that is invalid on its face: either
    endpoint out of bounds, or the start tile itself unwalkable (an agent
    cannot already be standing somewhere impassable). An unwalkable goal
    is NOT an error (a caller pathing toward a piece of furniture should
    use WorldMap.nearest_walkable or WorldMap.entry_point to pick an
    adjacent standable tile first) - it simply returns None, since no
    walkable path can end on an unwalkable tile.

    Step cost is STREET_STEP_COST (1) for an orthogonal move onto a
    street tile, doubled (OFF_STREET_COST_MULTIPLIER) for a move onto any
    other walkable tile when prefer_streets is on (the default - module
    docstring); allow_diagonal (off by default, see walkability.py's
    module docstring) doubles whatever that base cost is for a diagonal
    step, so a diagonal is never cheaper than two orthogonal steps
    covering the same displacement. Either way the minimum possible
    per-step cost stays exactly STREET_STEP_COST (an orthogonal street
    step), matching what manhattan_distance already assumes as its unit,
    so the heuristic never overestimates true remaining cost and A* still
    returns a truly cheapest path, not an approximation - prefer_streets
    changes which path is cheapest, never whether A* finds the cheapest
    one. Passing prefer_streets=False recovers the plain unweighted
    behavior (every walkable tile costs the same to enter).
    """
    if not world.in_bounds(start):
        raise PathfindingError(f"start {start} is outside the {world.width}x{world.height} map")
    if not world.in_bounds(goal):
        raise PathfindingError(f"goal {goal} is outside the {world.width}x{world.height} map")
    if not world.is_walkable(start):
        raise PathfindingError(f"start {start} is not walkable")

    if start == goal:
        return [start]
    if not world.is_walkable(goal):
        return None

    frontier: list[tuple[int, int, TileCoord]] = [(0, 0, start)]
    came_from: dict[TileCoord, TileCoord] = {}
    cost_so_far: dict[TileCoord, int] = {start: 0}
    tie_breaker = 0
    neighbor_offsets = neighbors8 if allow_diagonal else neighbors4

    while frontier:
        _, _, current = heapq.heappop(frontier)
        if current == goal:
            return reconstruct_path(came_from, current)

        for nx, ny in neighbor_offsets(current.x, current.y):
            if not in_bounds_xy(world.width, world.height, nx, ny):
                continue
            if world.collision[ny][nx]:
                continue
            neighbor = TileCoord(nx, ny)
            step_cost = 2 * STREET_STEP_COST if (nx != current.x and ny != current.y) else STREET_STEP_COST
            if prefer_streets and not world.is_street(neighbor):
                step_cost *= OFF_STREET_COST_MULTIPLIER
            new_cost = cost_so_far[current] + step_cost
            if new_cost < cost_so_far.get(neighbor, math.inf):
                cost_so_far[neighbor] = new_cost
                came_from[neighbor] = current
                priority = new_cost + manhattan_distance(neighbor, goal)
                tie_breaker += 1
                heapq.heappush(frontier, (priority, tie_breaker, neighbor))

    return None
