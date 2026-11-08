"""move: the one primitive with no location gate of its own - DESIGN.md's
whole location-gating premise ("you must be at the mill to mill") only
makes sense if getting from A to B is itself unrestricted, gated only by
being alive and not dormant like any other primitive.

Task "walking": agents must WALK to destinations along the town's
walkable tiles, never teleport. world.core.movement.MovementSystem
(built once, over the real Kasukabe genesis map, by bootstrap.build_world
- see that module's docstring) is the state machine that already does
this correctly (18 passing tests, a genuine 113-tile cross-town walk);
this handler's whole job is starting a route on it instead of publishing
one instant-jump `agent.moved` the way it used to. It never advances
that route itself - `advance()` is a tick loop's job (world.core.engine.
TickEngine, once a caller wires `movement_system=` into it - see that
module's docstring for the exact one-line change two composition roots
still need), so a real walk visibly takes many ticks, exactly as
DESIGN.md requires.

Real map integration is necessarily partial, not total, because most of
this codebase still places agents at opaque, non-map location_id strings
(run_genesis.py's `home:{household_id}`, this package's own locations.py
constants like "mill"/"temple" for institutions the base Ville map does
not contain - see that module's own docstring on why that integration is
still future work for tools OTHER than move). So this handler resolves
against the real WorldMap - via MovementSystem.world_map.entry_point,
exactly the lookup world.map.model.WorldMap's own docstring says a
"go to <sector>" request should use - and only when BOTH the
destination name AND the agent's current position resolve to a real
tile does it start a genuine ticked route; anything that does not
resolve (an opaque placeholder location_id, an agent whose position has
never touched the real map) falls back to the pre-walking instant jump
unchanged, so no other tool/test that already depends on opaque
location_id semantics regresses. A destination that DOES resolve to a
real tile but has no walkable route to it (a genuinely unreachable
sector) is reported as such rather than teleported to - "no location
gate" means `move` is never refused for being in the wrong place, not
that it fabricates a route that does not exist.

`agent.moved` is core.perception's own payload-key convention
(core/perception.py: "Every world/ submodule that moves or spawns an
agent is expected to publish `agent.moved` ... with a string `agent_id`
and `location_id` payload"); MovementSystem's own per-step events already
satisfy that shape and add `tile_x`/`tile_y`/`arrived` on top (see that
module's docstring) - `world.core.perception.LocationIndex`, once
attached to the same EventBus (bootstrap.py does this once at
construction), updates itself from every such event exactly like before,
so this handler still never touches `world.locations` directly.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.core.movement import MovementSystem, estimate_travel_ticks
from world.map.pathfinding import find_path
from world.life.models import AgentLifeRecord
from world.map.model import TileCoord
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.place_aliases import satisfies_gate
from world.tools.state import WorldState


class MoveParams(BaseModel):
    destination_location_id: str = Field(min_length=1)


def resolve_move_distance(world: WorldState, actor_id: str, params: Any) -> int:
    """How many ticks a `move` call is about to cost, resolved BEFORE it runs.

    ECON-18. Returns ticks, for the field spelled `distance_tiles` - see
    this module's note on units at the call site in `registry.py`.

    Starts nothing and writes nothing: this runs on the charging path,
    where the walk has not been agreed to yet. Returns 0 for anything it
    cannot resolve (no map, unknown destination, no route), which falls
    back to exactly the old behaviour - the 1-sim floor - rather than
    inventing a distance and overcharging for a walk that will not happen.
    """
    movement = getattr(world, "movement", None)
    if movement is None:
        return 0
    fields = params.model_dump() if isinstance(params, BaseModel) else dict(params or {})
    destination = fields.get("destination_location_id")
    if not destination:
        return 0

    # Already walking there: charge for what is LEFT, not for the whole
    # route again. `move` re-issued at the same destination is a no-op
    # that returns `already_walking_there` (see the handler below), and
    # billing the full trip for it would make asking "am I nearly there"
    # cost more than the journey.
    journey = movement.journey_for(actor_id)
    if journey is not None and journey.destination_location_id == destination:
        return estimate_travel_ticks(movement.world_map, journey.path[journey.step_index:])

    target = movement.world_map.entry_point(destination)
    if target is None:
        return 0
    if not movement.world_map.is_walkable(target):
        target = movement.world_map.nearest_walkable(target)
        if target is None:
            return 0
    start = resolve_current_tile(world, movement, actor_id)
    if start is None:
        return 0
    # The same route the walk itself will take: `start_journey` computes
    # it with prefer_streets=True, and an estimate off a different route
    # than the one walked is a bill for a journey nobody made.
    path = find_path(movement.world_map, start, target, prefer_streets=True)
    if not path:
        return 0
    return estimate_travel_ticks(movement.world_map, path)


def resolve_current_tile(world: WorldState, movement: MovementSystem, agent_id: str) -> TileCoord | None:
    """Best-effort "where is this agent standing right now, in tile
    terms" for starting a new route, tried in order of precision:

    1. An in-progress journey's current tile (MovementSystem.
       journey_for) - the agent is mid-walk, so this is exact and always
       walkable (part of a live, already-validated path). A fresh `move`
       call replacing an in-progress one is exactly MovementSystem.
       start_journey's own documented behavior ("a fresh destination
       simply is a fresh journey").
    2. The last tile a genuine walked step reported, world.agent_tiles
       (bootstrap.attach_tile_tracking's own doing) - also always
       walkable by construction (every tile MovementSystem ever reports
       came off a real find_path route).
    3. The agent's current LocationIndex string, resolved back to a real
       map tile via WorldMap.entry_point - only works when that string
       IS a real sector/arena name, which most of this codebase's
       location_id strings still are not (module docstring). Guards with
       is_walkable/nearest_walkable itself rather than trusting
       entry_point's spawn-location branch unconditionally (that branch
       does not itself check walkability - see WorldMap.entry_point's
       own docstring), so a caller of this function never hands
       MovementSystem.start_journey a `start` it would raise
       PathfindingError over.

    Returns None when nothing resolves - the caller's cue that this
    agent has no known real-map position yet, so a `move` call should
    fall back to the pre-walking opaque jump instead of guessing one.
    """
    journey = movement.journey_for(agent_id)
    if journey is not None:
        return journey.current_tile

    recorded = world.agent_tiles.get(agent_id)
    if recorded is not None:
        return TileCoord(*recorded)

    current_location_id = world.location_of(agent_id)
    if not current_location_id:
        return None
    tile = movement.world_map.entry_point(current_location_id)
    if tile is None:
        return None
    if movement.world_map.is_walkable(tile):
        return tile
    return movement.world_map.nearest_walkable(tile)


def opaque_jump_outcome(world: WorldState, agent: AgentLifeRecord, destination_location_id: str) -> ToolOutcome:
    """The pre-walking behaviour, the fallback for any destination (or
    agent position) that does not resolve against the real map.

    TOOL-05. `move` is two mechanisms and the resident was never told
    which it got. A walk returns `walking: true` and an `estimated_ticks`;
    a jump returned `{"location_id": X}` and nothing else, so a model
    read teleport and walk as the same act - and every `home:*`,
    `business:*`, workshop and resource-family id is a jump, as is the
    first move of every run, because a genesis resident starts at a
    `home:*` with no tile. Run 23: 170 `look_around` results with
    `"tile": null` against 3 with a tile.

    The shape now says so. `walked: false` and a note in the resident's
    own terms, because "you stepped straight there; it has no route on
    the map" is a fact about the town it can act on - it explains why a
    journey it expected to take ticks took none, and why `look_around`
    will not show it a tile.
    """
    event = WorldEvent(
        type="agent.moved",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "location_id": destination_location_id,
            "arrived": True,
            "walked": False,
            # TOOL-05(a). `default_interrupt_policy` wakes a resident on
            # an event naming it in `target_agent_id`; `agent.moved`
            # carried `agent_id` only, so nothing about arriving anywhere
            # ever interrupted the scheduler. An arrival is the one
            # moment a resident most needs its next turn.
            "target_agent_id": agent.agent_id,
        },
    )
    return ToolOutcome(
        result={
            "location_id": destination_location_id,
            "walked": False,
            "walking": False,
            "note": (
                f"You stepped straight to {destination_location_id}; it has no "
                "route on the map, so the walk took no time."
            ),
        },
        events=[event],
    )


def _nearest_places(world: WorldState, wanted: str) -> list[str]:
    """The handful of real places most like what the agent asked for."""
    from world.tools.primitives.places import known_places

    words = {w for w in re.split(r"[^a-z0-9]+", wanted.lower()) if len(w) > 2}
    scored = []
    for place in known_places(world):
        place_id = place["place_id"]
        other = {w for w in re.split(r"[^a-z0-9]+", place_id.lower()) if len(w) > 2}
        overlap = len(words & other)
        if overlap:
            scored.append((-overlap, place_id))
    scored.sort()
    near = [p for _, p in scored[:5]]
    # Nothing resembled it: offer what is nearby instead of a silent list,
    # so the reply is useful even when the guess was nonsense.
    if not near:
        near = [p["place_id"] for p in known_places(world)][:5]
    return near


def canonical_destination(world: WorldState, wanted: str) -> str:
    """The town's own spelling of a place the agent is trying to reach.

    Agents type variants constantly, and every variant used to become its
    own place. Observed live, all at one moment, with residents standing
    alone in each:

        'willows_market_pharmacy'  vs  'The Willows Market and Pharmacy'
        'h_quarry'                 vs  'home:h_quarry'
        'harvey_oak_supply_store'  vs  'Harvey Oak Supply Store'
        'Farmland'                 vs  'Kasukabe Farmland'

    Two residents in one shop who spelled it differently could not see each
    other, and `say` - which gates on location - refused speech between
    them.

    This FOLDS and never refuses. Three earlier attempts tried to reject an
    unrecognised destination and each broke somewhere a resident could
    legitimately stand: `temple`/`mill`/`farmland:wheat` (gated tool
    locations with no map sector), `grocery-shop` (a business naming its own
    premises), then `square` and `park`. The town's vocabulary is larger
    than this module can enumerate, so the only safe move is to improve the
    match and leave anything still unmatched alone.

    Folding is safe in a way refusing is not: the result is always a place
    that already exists, and a miss changes nothing.
    """
    from world.tools.primitives.places import known_places

    def words(text: str) -> list[str]:
        # Prefixes agents attach that carry no meaning of their own -
        # "loc_college", "L_selenes", "place:t_temple". The bare name is
        # what has to match.
        text = re.sub(r"^(loc|l|place|sector|the)[_:\s]+", " ", text.lower())
        return [w for w in re.split(r"[^a-z0-9]+", text) if w]

    target = words(wanted)
    if not target:
        return wanted
    target_set = set(target)

    from world.tools.place_aliases import known_tool_locations

    ids = [p["place_id"] for p in known_places(world)]
    # Locations the TOOLS mint and gate on, which known_places does not list
    # because they have no map sector: "temple", "mill", "farmland:wheat".
    # Without them "temple" folded into "Kasukabe Temple" and every gate
    # that wanted "temple" stopped opening - the precise breakage
    # place_aliases.satisfies_gate exists to prevent, reintroduced one layer
    # up.
    ids += sorted(known_tool_locations())
    # Records the world mints per row, which known_places does not list.
    ids += [f"home:{h}" for h in getattr(world, "households", {})]
    ids += [
        getattr(b, "location_id", None) or f"business:{bid}"
        for bid, b in getattr(world, "businesses", {}).items()
    ]

    # A name the town already has is never folded into a longer one.
    #
    # "Oak Hill College" is a real sector and its words are a subset of
    # "Dorm for Oak Hill College" - so the subset rule below swallowed it
    # and sent every student to the dorm. An exact match wins outright.
    for place_id in ids:
        if place_id == wanted:
            return wanted
    for place_id in ids:
        if place_id and set(words(place_id)) == target_set and len(words(place_id)) == len(target):
            return place_id

    exact, subset = [], []
    for place_id in ids:
        if not place_id or place_id == wanted:
            continue
        other = set(words(place_id))
        if not other:
            continue
        if other == target_set:
            exact.append(place_id)
        elif target_set < other or other < target_set:
            # One name is the other with a word or two more: "willows market
            # pharmacy" against "willows market and pharmacy", "h quarry"
            # against "home h quarry".
            subset.append(place_id)

    if len(exact) == 1:
        return exact[0]
    # Only when there is exactly one candidate. Two possible shops is not a
    # spelling to fix, it is a guess to decline.
    if not exact and len(subset) == 1:
        return subset[0]
    # Several candidates, exactly one of them a sector.
    #
    # "l_outcrop" is a subset of "Kasukabe Ore Outcrop" AND of every
    # "ore_outcrop:<metal>" - four candidates, so the one-candidate rule
    # above declined and the agent stood in a place of its own invention
    # while `mine` refused it. The sector is the answer that commits to
    # least: it is where the map would have drawn them anyway, and (per
    # place_aliases.SECTOR_CONTAINS_FAMILY) it satisfies the gates of every
    # variety inside it, so nothing is lost by not guessing which one.
    if not exact and len(subset) > 1:
        sectors = [place_id for place_id in subset if ":" not in place_id]
        if len(sectors) == 1:
            return sectors[0]

    # A "family:variant" name whose variant the town does not have.
    #
    # "farmland:general" is shaped exactly like the real "farmland:wheat",
    # but 'general' is not one of the ten crop varieties, so no rule above
    # matched a single word of it and the agent stood in a farmland of its
    # own invention - close enough to the real one to read correctly in a
    # log, far enough that nobody could hear a word it said. Observed live:
    # Tobias spent his morning at "farmland:general" while Wren stood in
    # "Kasukabe Farmland", and all three times he spoke to her the world
    # refused it as speech across town.
    #
    # Folded to the SECTOR, never to a sibling variant: sending someone who
    # asked for farmland generally into "farmland:wheat" would put them in
    # one specific crop strip they never named. And only when the family
    # picks out exactly one sector - the same "one candidate or nothing"
    # discipline the rules above hold to.
    if ":" in wanted:
        family = set(words(wanted.split(":", 1)[0]))
        if family:
            sectors = [
                place_id
                for place_id in ids
                if place_id and ":" not in place_id and family <= set(words(place_id))
            ]
            if len(sectors) == 1:
                return sectors[0]

    # A resource site named after its resource.
    #
    # The town names these places after the thing you get there -
    # "farmland:wheat", "ore_outcrop:copper" - so a model that names the
    # resource has named the place, whatever it wrapped around it.
    # Observed live: Bram spent his morning walking to "loc_quarry_mine"
    # and then "loc_mine_copper", and `mine` refused him both times with
    # "requires location 'ore_outcrop:copper'" - the tool naming the exact
    # place he was trying to reach while he stood in an invented one.
    #
    # Word-level matching, so "corner" is not "corn". Exactly one resource
    # or nothing: "wheat and barley" names two fields and is a guess to
    # decline, the same discipline as the rules above.
    from world.map.model import CROP_VARIETIES, ORE_METALS
    from world.tools import locations as loc

    named_crops = [c for c in CROP_VARIETIES if c in target_set]
    named_metals = [m for m in ORE_METALS if m in target_set]
    if len(named_crops) == 1 and not named_metals:
        return loc.farmland_location(named_crops[0])
    if len(named_metals) == 1 and not named_crops:
        return loc.ore_outcrop_location(named_metals[0])

    # A place named by its ONE distinctive word, when the rest of what the
    # agent typed is a synonym the town does not use.
    #
    # Every rule above matches on whole word-sets, so it needs the agent's
    # words to be a subset or superset of the real name's. `loc_shop_willows`
    # is neither: "willows" is right and "shop" appears in no place name the
    # town has. Delia Okonjo walked there in run 19 and spent the run alone
    # in it, while Silas stood in "The Willows Market and Pharmacy" asking
    # how to buy something and the pharmacist he needed heard nothing.
    #
    # A word is DISTINCTIVE when exactly one place in the whole town uses
    # it, which is what makes this safe: "willows" can only mean one room,
    # so folding commits to nothing the agent did not already say. Words
    # shared by several places decide nothing and are ignored - the same
    # "one candidate or nothing" discipline as every rule above.
    distinctive: dict[str, list[str]] = {}
    for place_id in ids:
        if not place_id:
            continue
        for word in set(words(place_id)):
            distinctive.setdefault(word, [])
            if place_id not in distinctive[word]:
                distinctive[word].append(place_id)
    candidates: list[str] = []
    for word in target_set:
        owners = distinctive.get(word, [])
        if len(owners) == 1 and owners[0] not in candidates:
            candidates.append(owners[0])
    if len(candidates) == 1:
        return candidates[0]

    # Several places share every word the agent used: prefer the place
    # itself over a place named after it.
    #
    # "college" belongs to both "Oak Hill College" and "Dorm for Oak Hill
    # College", so no rule above could choose and Liora Thorne spent run 19
    # at `l_college`, alone. The shorter name is the institution; the longer
    # one is a building that refers to it. Only when one name's words are
    # wholly contained in the other's, so this never picks between two
    # unrelated places that happen to share a word.
    shared = [
        place_id
        for place_id in ids
        if place_id and target_set and target_set <= set(words(place_id))
    ]
    if len(shared) > 1:
        shortest = min(shared, key=lambda place_id: len(words(place_id)))
        if all(
            set(words(shortest)) <= set(words(other)) for other in shared
        ):
            return shortest
    return wanted


async def move_handler(world: WorldState, agent: AgentLifeRecord, params: MoveParams) -> ToolOutcome:
    # Fold a differently-spelled name onto the town's own, before anything
    # moves. Six people in a shop could not see the seventh because they
    # had walked to 'Harvey Oak Supply Store' and it to
    # 'harvey_oak_supply_store'.
    resolved = canonical_destination(world, params.destination_location_id)

    # Refuse a destination the town does not have.
    #
    # `move` allowed an opaque jump to ANY string, which is right for the
    # town's own non-sector locations ("temple", "mill", "farmland:wheat")
    # and wrong for everything else: agents invented ids and walked into
    # them. Observed live, alongside four residents correctly at "Harvey Oak
    # Supply Store" - 'store_harvey_oak', 'loc_farmland', 'place:t_temple',
    # 'marketplace', one resident each, every one of them standing alone in
    # a place of its own making where nobody could see or speak to them.
    # Deliberately NOT refused when the destination is unrecognised.
    #
    # Three attempts to enumerate "a real place" each broke working
    # behaviour: first `temple`/`mill`/`farmland:wheat` (tool locations with
    # gates and no map sector), then `grocery-shop` (a business naming its
    # own premises), then `square` and `park`. The town's location
    # vocabulary is larger than anything this module can list, and every
    # tightening refused somewhere a resident could legitimately stand.
    #
    # `move` therefore stays permissive, as it was designed to be. What is
    # fixed instead is the REPORTING: see run_genesis's _phrase_activity,
    # which used to read the requested destination and so announced "walking
    # to loc_college" for an agent standing at Oak Hill College.

    if resolved != params.destination_location_id:
        params = params.model_copy(update={"destination_location_id": resolved})

    movement = world.movement

    # Already walking there? Say so, and leave the walk alone.
    #
    # `move` restarted the journey from the agent's current tile every time
    # it was called, so an agent that re-issued the same destination walked
    # the first leg again and again and arrived nowhere. Measured by the
    # §4.1 eval on a live run: 9 of 13 journeys never finished, and six of
    # those were Marta Thorne calling `move` to the Government Complex five
    # times and the Temple once inside a single turn.
    #
    # It is also the mechanism the residents described in run 15 - "kept
    # ending up back at Hobbs", "the moves aren't sticking" - and no
    # instrument had ever counted it.
    #
    # Telling the agent how far is left matters as much as not restarting:
    # a result that just repeated "walking: true" gave it no reason to stop
    # asking.
    if movement is not None:
        current = movement.journey_for(agent.agent_id)
        if current is not None and current.destination_location_id == params.destination_location_id:
            remaining = estimate_travel_ticks(
                movement.world_map, current.path[current.step_index:]
            )
            return ToolOutcome(
                result={
                    "location_id": world.location_of(agent.agent_id),
                    "destination_location_id": params.destination_location_id,
                    "walking": True,
                    "already_walking_there": True,
                    "estimated_ticks": remaining,
                    "note": (
                        f"You are already walking to {params.destination_location_id} - "
                        f"about {remaining} tick(s) left. Asking again does not make it "
                        "sooner; do something else, or `rest` to stop."
                    ),
                },
                events=[],
            )

    if movement is not None:
        destination_tile = movement.world_map.entry_point(params.destination_location_id)
        if destination_tile is not None:
            start_tile = resolve_current_tile(world, movement, agent.agent_id)
            if start_tile is not None:
                journey = movement.start_journey_to_location(
                    agent.agent_id, start_tile, params.destination_location_id
                )
                if journey is None:
                    # A real, named destination with no walkable route to
                    # it from here - an ordinary outcome (MovementSystem.
                    # start_journey's own docstring), reported honestly
                    # rather than teleported to. "No location gate" means
                    # `move` is never refused for being in the wrong
                    # place, not that it fabricates a route.
                    return ToolOutcome(
                        result={
                            "location_id": world.location_of(agent.agent_id),
                            "destination_location_id": params.destination_location_id,
                            "walking": False,
                            "error": "no walkable route to that destination",
                        },
                        events=[],
                    )
                if journey.arrived:
                    # Already standing there (start == destination tile) -
                    # zero ticks needed, matching MovementSystem.
                    # start_journey's own "already there" contract. Still
                    # a real, tile-carrying agent.moved, not the opaque
                    # shape - a caller polling perception sees exactly
                    # where this agent is either way.
                    tile = journey.destination_tile
                    event = WorldEvent(
                        type="agent.moved",
                        tick=world.tick(),
                        payload={
                            "agent_id": agent.agent_id,
                            "location_id": journey.destination_location_id,
                            "tile_x": tile.x,
                            "tile_y": tile.y,
                            "arrived": True,
                            "walked": True,
                            # TOOL-05(a): arriving wakes you. See
                            # `opaque_jump_outcome` for why.
                            "target_agent_id": agent.agent_id,
                        },
                    )
                    return ToolOutcome(
                        result={
                        "location_id": journey.destination_location_id,
                        "walking": False,
                        "walked": True,
                    },
                        events=[event],
                    )
                # A genuine multi-tile route has been started. No event
                # publishes here - the agent has not taken a single step
                # yet (MovementSystem.start_journey never advances a
                # journey it creates) - a tick loop's own advance() calls
                # (world.core.engine.TickEngine, once wired - see this
                # module's docstring) publish one agent.moved per tile
                # crossed from here on, which is what makes this a real
                # walk instead of an instant jump.
                return ToolOutcome(
                    result={
                        "location_id": world.location_of(agent.agent_id),
                        "destination_location_id": journey.destination_location_id,
                        "walking": True,
                        "estimated_ticks": estimate_travel_ticks(movement.world_map, journey.path),
                    },
                    events=[],
                )
    # Fallback: destination_location_id (or this agent's own position)
    # is not yet a real map location - see this module's own docstring.
    return opaque_jump_outcome(world, agent, params.destination_location_id)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="move",
            description="Walk to another named location.",
            params_model=MoveParams,
            handler=move_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            # Part D: the one tool a confined resident cannot use. An
            # arrest that leaves the accused free to walk out of the cells
            # is a word rather than an arrest.
            actor_requirements=ActorRequirements(require_not_confined=True),
        )
    ]
