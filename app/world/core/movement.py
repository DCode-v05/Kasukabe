"""Movement/pathing engine - DESIGN.md's "agents must WALK to destinations
along the town's walkable tiles, never teleport or slide through walls."

Deliberately decoupled from world/tools/ (the ToolRegistry dispatch layer
that runs the `move` primitive today) and from world/core/engine.py's tick
loop - this task's own ownership boundary is exactly this module, the
pathing parts of world/map/, and the game client's sprite interpolation,
not the wiring that calls into them. MovementSystem is therefore a small,
self-contained, directly testable state machine with the handful of calls
a tick-loop owner needs to drive it:

    system = MovementSystem(world_map)
    journey = system.start_journey(agent_id, start=here, destination=there)
    ...
    events = system.advance(tick)   # call once per tick; publish the
                                     # returned WorldEvents on world's
                                     # EventBus exactly like every other
                                     # world/ submodule's mutation (see
                                     # world.core.engine.TickEngine.tick's
                                     # own `await self.event_bus.emit(...)`
                                     # calls for the established pattern).

Route selection: world.map.pathfinding.find_path (A* over the same
collision grid world.map.model.WorldMap already loaded from the Ville
CSVs), never a second pathing implementation - this module owns none of
the graph search itself, only turning a computed route into tick-by-tick
motion. find_path(..., prefer_streets=True) is what makes a route "prefer
roads and paths where they exist" in the first place (world.map.
pathfinding's own module docstring has the full argument); this module
reuses that same world.is_street() signal for its speed model below so
both halves of "prefer roads" (which tiles, how fast) agree with each
other.

Speed model (TICKS_PER_STREET_TILE / TICKS_PER_OFF_STREET_TILE):
DESIGN.md does not pin a walking speed, so this is a deliberately
explicit, documented, tunable assumption rather than a silently-picked
number. Crossing one street tile costs 1 tick (10 game-minutes,
world.core.clock.TICK_MINUTES); crossing one off-street tile (through a
building interior, or open ground with no path) costs twice that. Total
journey time is exactly the sum of its tiles' costs: genuinely
proportional to distance, never instant for any real distance, and long
enough that another agent standing at an intermediate tile's location can
perceive the traveler mid-route - every step publishes a real
`agent.moved` event, and world.core.perception.LocationIndex (which
already reacts generically to that event, unmodified) is what makes
"who is here right now" reflect the traveler's current tile for as long
as they are standing on it.

Collision and "never slide through walls" both fall directly out of
reusing find_path unmodified: the returned route is, by that module's own
contract, a sequence of orthogonally (or optionally diagonally) adjacent
walkable tiles from start to goal with no shortcuts, and advance() below
only ever moves an agent exactly one tile of that precomputed sequence
per completed countdown, never more, never off the sequence. A route is
computed once, at start_journey() time, against a snapshot of the map's
current collision grid; a same-tick land expansion invalidating an
in-flight route is a known, out-of-scope edge case (DESIGN.md section 1
calls land growth rare, not a per-tick concern), not something silently
mishandled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from world.core.events import WorldEvent
from world.map.model import TileCoord, WorldMap
from world.map.pathfinding import find_path

STREET_TILES_PER_TICK = 40
OFF_STREET_TILES_PER_TICK = 20

TICKS_PER_STREET_TILE = 1
TICKS_PER_OFF_STREET_TILE = 2

LocationIdResolver = Callable[[WorldMap, TileCoord], str]


def default_location_id(world_map: WorldMap, tile: TileCoord) -> str:
    """Best-effort location_id for a tile from map geometry alone: the
    sector name if the tile is inside one, else "street" for open public
    ground (WorldMap.is_street) or "outdoors" for any other walkable tile
    that belongs to no sector and isn't classified as street (a resource
    zone strip, say - world.map.model.ResourceKind sectors do have a
    Sector object and so are covered by the sector-name branch above;
    this fallback exists for a synthetic/partial map, not the real Ville
    map, where every non-street walkable tile does belong to some
    sector).

    world.tools.locations.py's own docstring says resolving a tile into
    one of *its* canonical slugs (LOCATION_TEMPLE and friends) is future
    integration work for "whoever wires world.map's WorldMap into this
    package" - this function is deliberately not that resolver. It is the
    default MovementSystem falls back to when no caller-supplied resolver
    is given, so this module has zero import-time dependency on
    world.tools; a caller that owns that integration can inject its own
    LocationIdResolver into MovementSystem's constructor instead of using
    this one.
    """
    sector = world_map.sector_at(tile)
    if sector is not None:
        return sector.name
    return "street" if world_map.is_street(tile) else "outdoors"


def tile_step_cost(world_map: WorldMap, tile: TileCoord) -> int:
    """Ticks to cross onto `tile` - see module docstring's speed model."""
    return TICKS_PER_STREET_TILE if world_map.is_street(tile) else TICKS_PER_OFF_STREET_TILE


def stride_length(world_map: WorldMap, tile: TileCoord) -> int:
    """How many tiles a resident crosses in one tick, standing on `tile`.

    Roads are faster than open ground, which is the same "prefer roads"
    signal the pathfinder uses, so both halves of the speed model agree.
    """
    return (
        STREET_TILES_PER_TICK
        if world_map.is_street(tile)
        else OFF_STREET_TILES_PER_TICK
    )


def estimate_travel_ticks(world_map: WorldMap, path: list[TileCoord]) -> int:
    """Total ticks a route (as returned by find_path, inclusive of both
    ends) takes to walk end to end - the same total advance() eventually
    charges over however many calls it takes, computed up front for a
    caller that wants to know "how long will this take" (e.g. a `move`
    tool result) without driving advance() itself. The start tile is
    never "entered" (the agent is already standing there), so only
    path[1:] counts, matching Journey's own step-cost bookkeeping.
    """
    ticks = 0
    index = 1
    while index < len(path):
        index += stride_length(world_map, path[index - 1])
        ticks += 1
    return ticks


@dataclass
class Journey:
    """One agent's in-progress walk: a precomputed route plus how many
    ticks remain until the agent steps onto the next tile.

    `path` is inclusive of both the start and destination tile
    (find_path's own contract) and is never recomputed mid-walk - see
    the module docstring's note on land-expansion being out of scope.
    A Journey with a single-tile path (start == destination) is always
    already `arrived`; MovementSystem.start_journey never hands one of
    those to advance() (nothing to advance), so advance() itself never
    needs to special-case it.
    """

    agent_id: str
    path: list[TileCoord] = field(default_factory=list)
    destination_location_id: str = ""
    step_index: int = 0
    ticks_remaining: int = 0

    @property
    def current_tile(self) -> TileCoord:
        return self.path[self.step_index]

    @property
    def destination_tile(self) -> TileCoord:
        return self.path[-1]

    @property
    def arrived(self) -> bool:
        return self.step_index >= len(self.path) - 1


class MovementSystem:
    """Drives every in-progress walk. One instance per running world
    process (bootstrap.py's job to construct and hold and world.core.
    engine.TickEngine's job to call once per tick - both out of this
    module's scope, see module docstring); this class has no dependency
    on either.
    """

    def __init__(
        self,
        world_map: WorldMap,
        *,
        location_id_resolver: LocationIdResolver = default_location_id,
    ) -> None:
        self.world_map = world_map
        self.location_id_resolver = location_id_resolver
        self.journeys: dict[str, Journey] = {}

    def has_active_journey(self, agent_id: str) -> bool:
        return agent_id in self.journeys

    def journey_for(self, agent_id: str) -> Journey | None:
        return self.journeys.get(agent_id)

    def cancel_journey(self, agent_id: str) -> None:
        """Stops an agent's in-progress walk wherever they currently are
        (does not snap them back to the start or forward to the
        destination). A fresh start_journey() call for the same agent
        already replaces any journey in progress without needing this
        first; this exists for a caller that wants to stop a walk with no
        new destination in mind."""
        self.journeys.pop(agent_id, None)

    def start_journey(
        self,
        agent_id: str,
        start: TileCoord,
        destination: TileCoord,
        *,
        destination_location_id: str | None = None,
        allow_diagonal: bool = False,
    ) -> Journey | None:
        """Computes a route with find_path(..., prefer_streets=True) and
        begins walking it, replacing any journey already in progress for
        this agent (DESIGN.md gives no "can't change your mind mid-walk"
        rule - a fresh destination simply is a fresh journey).

        Raises PathfindingError (re-exported from world.map.pathfinding,
        not caught here) for a request invalid on its face: start out of
        bounds or unwalkable. A caller one layer up (the `move` tool
        handler, out of this module's scope) is expected to have already
        resolved a location_id into a walkable TileCoord - e.g. via
        WorldMap.entry_point/nearest_walkable - before reaching here, so
        those really are caller bugs, not ordinary outcomes; see
        start_journey_to_location below for a convenience wrapper that
        does that resolution itself.

        Returns None (no journey started) in exactly the two cases
        find_path itself treats as ordinary non-error outcomes: the
        destination is unreachable from the start, or the destination
        tile itself is unwalkable. Returns a Journey with `arrived` True
        and nothing registered in self.journeys when start == destination
        (already there - zero ticks needed, nothing for advance() to do).
        """
        path = find_path(
            self.world_map, start, destination, allow_diagonal=allow_diagonal, prefer_streets=True
        )
        if path is None:
            self.journeys.pop(agent_id, None)
            return None

        location_id = destination_location_id or self.location_id_resolver(
            self.world_map, destination
        )
        journey = Journey(
            agent_id=agent_id,
            path=path,
            destination_location_id=location_id,
            step_index=0,
            ticks_remaining=1 if len(path) > 1 else 0,
        )

        if journey.arrived:
            self.journeys.pop(agent_id, None)
        else:
            self.journeys[agent_id] = journey
        return journey

    def start_journey_to_location(
        self,
        agent_id: str,
        start: TileCoord,
        sector_name: str,
        arena_name: str | None = None,
    ) -> Journey | None:
        """Convenience wrapper for the case DESIGN.md itself describes
        ("when an agent chooses a destination"): resolves a named
        sector/arena to a walkable tile via WorldMap.entry_point, falling
        back to WorldMap.nearest_walkable if that footprint has no
        walkable tile of its own, then starts a journey there tagged with
        the sector name as the arrival location_id.

        Returns None if the name does not resolve to any tile at all
        (unknown sector/arena) or no walkable tile could be found near
        it - both ordinary "can't get there" outcomes, matching
        start_journey's own contract for an unreachable destination.
        """
        target = self.world_map.entry_point(sector_name, arena_name)
        if target is None:
            return None
        if not self.world_map.is_walkable(target):
            target = self.world_map.nearest_walkable(target)
            if target is None:
                return None
        return self.start_journey(agent_id, start, target, destination_location_id=sector_name)

    def advance(self, tick: int) -> list[WorldEvent]:
        """Advances every in-progress journey by exactly one tick.

        An agent whose countdown reaches zero this tick steps onto the
        next tile of their precomputed route and one `agent.moved`
        WorldEvent is appended for them, payload-shaped to satisfy both
        world.core.perception.LocationIndex's contract (`agent_id` +
        `location_id`) and game/static/js/town.js's `agent.moved` handler
        (`agent_id` + `tile_x` + `tile_y`) in the same event, plus
        `arrived` so a caller can tell a mid-route step from a final one
        without comparing tiles itself. An agent still mid-countdown
        (still crossing the current tile) produces no event this tick -
        "advance the agent along that route tile by tile across ticks"
        means exactly one tile per completed countdown, never a partial
        tile and never more than one tile in a single tick.

        Iterates agent_ids in sorted order so the returned event list is
        deterministic regardless of dict insertion order, matching the
        discipline world.tools.registry.ToolRegistry already applies
        elsewhere in world/ for reproducible output.
        """
        events: list[WorldEvent] = []
        finished: list[str] = []

        for agent_id in sorted(self.journeys):
            journey = self.journeys[agent_id]
            if journey.ticks_remaining > 0:
                journey.ticks_remaining -= 1
            if journey.ticks_remaining > 0:
                continue

            stride = stride_length(self.world_map, journey.current_tile)
            arrived = False
            for _ in range(stride):
                journey.step_index += 1
                tile = journey.current_tile
                arrived = journey.arrived
                location_id = (
                    journey.destination_location_id
                    if arrived
                    else self.location_id_resolver(self.world_map, tile)
                )
                payload = {
                    "agent_id": agent_id,
                    "location_id": location_id,
                    "tile_x": tile.x,
                    "tile_y": tile.y,
                    "arrived": arrived,
                    "walked": True,
                }
                if arrived:
                    payload["target_agent_id"] = agent_id
                events.append(WorldEvent(type="agent.moved", tick=tick, payload=payload))
                if arrived:
                    break

            if arrived:
                finished.append(agent_id)
            else:
                journey.ticks_remaining = 1

        for agent_id in finished:
            del self.journeys[agent_id]

        return events


    def snapshot(self) -> dict[str, Any]:
        return {
            agent_id: {
                "path": [[tile.x, tile.y] for tile in journey.path],
                "destination_location_id": journey.destination_location_id,
                "step_index": journey.step_index,
                "ticks_remaining": journey.ticks_remaining,
            }
            for agent_id, journey in self.journeys.items()
        }

    def restore(self, data: dict[str, Any]) -> None:
        self.journeys = {
            agent_id: Journey(
                agent_id=agent_id,
                path=[TileCoord(x, y) for x, y in entry["path"]],
                destination_location_id=entry["destination_location_id"],
                step_index=entry["step_index"],
                ticks_remaining=entry["ticks_remaining"],
            )
            for agent_id, entry in data.items()
        }
