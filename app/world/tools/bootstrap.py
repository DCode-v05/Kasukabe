"""build_world(): the one call site that wires world.ledger, world.charter,
world.core (event bus + location index + movement), and world.tools into
one running `(WorldState, ToolRegistry)` pair.

DESIGN.md section 12's build path is "world+tools -> broker+ledger ->
herald seam -> Supabase -> game app -> ...": this function IS that first
step made concrete. Anything that needs a working world (this package's
own integration tests, a future world/app.py HTTP layer, a future
10-agent local validation run) calls this instead of constructing
Ledger/CharterEngine/EventBus/LocationIndex/WorldState by hand - see each
sibling module's own docstring for why they were built not to depend on
each other directly (life/ports.py's EconomyPort, charter/models.py's
"the future tool dispatcher" comment, core/events.py's EventBus seam).
This module is where those seams finally close.

Movement (task "walking"): world.core.movement.MovementSystem needs a
real world.map.model.WorldMap to path against. This function loads
Kasukabe's real genesis map (world.map.genesis.build_kasukabe_genesis_map
- government/temple/resource zones minted, genesis-active sectors
classified, the town's actual walkable geometry) from disk once, builds
one MovementSystem over it, and hands it to WorldState.movement so
world.tools.primitives.movement.move_handler can reach it - see that
module's docstring for how a `move` call actually uses it. A caller that
already has a WorldMap loaded elsewhere (world.wiring.build_world_server's
own MapService, say) can pass `world_map=` directly instead of paying to
load a second copy; a caller with no Ville assets on disk at all (most of
this package's own unit tests, run in Docker where they ARE mounted -
see docker-compose.yml - but not guaranteed everywhere) gets `world.
movement = None` and a logged warning rather than a hard failure, so
`build_world()` keeps working exactly as it always has for anyone who
does not care about real walking - see load_movement_world_map()'s own
docstring.
"""

from __future__ import annotations

import time

import dataclasses
import logging
import random
from pathlib import Path

from world.charter.engine import create_genesis_charter_engine
from world.core.events import EventBus, WorldEvent
from world.core.movement import MovementSystem
from world.core.perception import LocationIndex
from world.gap_feedback import GapFeedback
from world.ledger.action_costs import DEFAULT_SCHEDULE
from world.ledger.head_tax import HeadTaxDebtStore
from world.ledger.core import Ledger
from world.map.genesis import build_kasukabe_genesis_map
from world.map.model import WorldMap
from world.tools import cost_disclosure, meta
from world.tools.economy_bridge import ledger_event_to_world_event
from world.tools.primitives import (
    goals,
    institutions,
    intent,
    labour,
    living,
    civic,
    commerce,
    construction,
    conveyance,
    crime,
    justice,
    movement,
    people,
    places,
    production,
    property,
    resources,
    social,
)
from world.tools.registry import ToolRegistry
from world.tools.state import WorldState

logger = logging.getLogger(__name__)

# app/world/tools/bootstrap.py -> parents[3] is the kasukabe/ repo root,
# matching world/wiring.py's own REPO_ROOT (PACKAGE_DIR.parent.parent from
# world/wiring.py - same two-hops-above-app/ destination, just walked from
# one directory deeper here) and world/map/tests/conftest.py's identical
# walk-up-parents trick, so this resolves the same way on a host checkout
# and inside the "test" docker-compose service (which mounts the repo's
# sibling smallville/ at /smallville - see that file's own comment).
DEFAULT_VILLE_DIR = (
    Path(__file__).resolve().parents[3]
    / "smallville"
    / "environment"
    / "frontend_server"
    / "static_dirs"
    / "assets"
    / "the_ville"
)

# One module per primitive family (primitives/__init__.py's convention).
# Order does not matter for correctness (every ToolSpec name is unique,
# checked by ToolRegistry.register); listed in roughly DESIGN.md section
# 2's own primitive order for readability.
PRIMITIVE_MODULES = (
    institutions,
    intent,
    # ENG-24, immediately after intent because it is the same family
    # grown up: an intention is one sentence about the next few ticks,
    # a goal has a horizon, a priority, progress and a way to be
    # superseded. Both stay - a resident should be able to note "going
    # to the mill" without declaring a goal about it.
    goals,
    labour,
    living,
    movement,
    # Immediately after movement: an agent that can walk needs to know where
    # it can walk TO. Without these three the town had 27 tools and no way
    # to answer "where can I go?", so an agent could only name places its
    # persona already mentioned.
    places,
    # And, immediately after places, who. `places` answered "where can I go";
    # nothing answered "who can help me", so an agent holding four units of
    # copper asked the room five times in one tick while a trader stood two
    # buildings away. Discovery is the other half of that pair.
    people,
    resources,
    production,
    commerce,
    # Land ownership sits with commerce, not construction: The 250 section
    # 31's unit of ownership is the 1x1 tile and its transfer is the same
    # shape as `transfer`, while construction.py's Plot is a development
    # RIGHT the government grants over a footprint - two different facts
    # about the same ground.
    property,
    civic,
    social,
    crime,
    construction,
    conveyance,
    justice,
)


def load_movement_world_map(ville_dir: Path) -> WorldMap | None:
    """Loads Kasukabe's real genesis map for MovementSystem to path
    against, or returns None (with a logged warning) when the Ville asset
    directory is not present - the same graceful-degradation rule
    world.wiring.build_world_server() already applies to its own
    MapService (see that module's `require_map` flag), extended here so
    every existing build_world() caller keeps working with no Ville
    assets required, just without real ticked walking until they are
    present (world.tools.primitives.movement.move_handler's own fallback
    path).

    A directory that DOES exist but fails to parse is a genuine bug
    (corrupt or unexpectedly-shaped source data), not a missing mount -
    that exception is deliberately not caught here and propagates, per
    this repo's hard-fail-on-genuine-error convention (see e.g.
    world.wiring's own `require_map` docstring on the same distinction).
    """
    if not ville_dir.exists():
        logger.warning(
            "Ville asset directory not found at %s - build_world() is booting with "
            "WorldState.movement = None; `move` will keep publishing an instant location "
            "jump instead of a real walked route until this directory is present.",
            ville_dir,
        )
        return None
    world_map, _zone_ids = build_kasukabe_genesis_map(ville_dir)
    return world_map


# One game-day of a talkative 50-agent town, roughly. The viewer shows the
# tail; the full history lives in the `utterances` trace table.
MAX_UTTERANCES_KEPT = 2000



def display_name(world: WorldState, agent_id: "str | None") -> "str | None":
    """This resident's name, or their id when the town has none for them.

    Reads `world.agent_names`, which is the ONLY place a name lives.

    It used to read `getattr(agent, "name", None)` off the AgentLifeRecord in
    `world.agents`, and that record has no `name` field and never has - its
    own docstring says so. So the getattr answered its default every time,
    and every utterance ever recorded stored the speaker's ID as their name.

    Nobody noticed for the life of the project because every surface that
    DISPLAYS an utterance resolves names from somewhere else: the viewer from
    its own roster, `/world/utterances` from the running genesis. The one
    consumer that trusted the stored value was the open-questions section,
    which rendered "n01 (n01) asked you..." - and ROADMAP.md already records
    what bare ids do to a town: nine utterances in 933 ticks, because a model
    cannot greet a0031.

    Build Spec v2 clause 4.4 exists for exactly this shape: a default that
    reads like data.
    """
    if not agent_id:
        return None
    names = getattr(world, "agent_names", None) or {}
    return names.get(agent_id) or agent_id


def attach_utterance_log(world: WorldState) -> None:
    """Keep the tail of what the town has said, for the viewer's Talk feed
    and for the open-questions query that reads it back.

    Its own function rather than a closure inside attach_tile_tracking,
    because the name bug lived in this subscriber for the whole life of the
    project and no test could reach it there.
    """

    async def on_utterance(event: WorldEvent) -> None:
        if event.type != "agent.utterance.spoken":
            return
        payload = event.payload
        speaker = payload.get("speaker_agent_id")
        world.utterances.append(
            {
                "tick": event.tick,
                # Wall clock as well as tick. The tick locates a remark
                # inside the simulation; only this locates it inside the
                # run, which is what a reader needs to line a conversation
                # up against the API calls that produced it.
                "said_at": time.time(),
                "speaker_agent_id": speaker,
                "speaker_name": display_name(world, speaker),
                "listener_agent_id": payload.get("listener_agent_id"),
                "listener_name": display_name(world, payload.get("listener_agent_id")),
                "location_id": payload.get("location_id"),
                "content": payload.get("content", ""),
                "is_public": payload.get("is_public", True),
                "conversation_id": payload.get("conversation_id"),
                # Everyone the remark spoke to, not only the parameter's
                # single id. open_questions and awaiting_reply both read
                # this to decide who is still owed an answer - see
                # social.everyone_addressed for the run-19 case it fixes.
                "addressed_agent_ids": list(payload.get("addressed_agent_ids") or []),
                # A2A intent: why the speaker said it, when it said. Research
                # data; no perception reads this row's `intent`.
                "intent": payload.get("intent"),
            }
        )
        if len(world.utterances) > MAX_UTTERANCES_KEPT:
            del world.utterances[: len(world.utterances) - MAX_UTTERANCES_KEPT]

    world.event_bus.subscribe("agent", on_utterance)


def attach_tile_tracking(world: WorldState) -> None:
    """Keeps `world.agent_tiles` in sync with every real `agent.moved`
    event that carries `tile_x`/`tile_y` (world.core.movement.
    MovementSystem's own event shape, published tick by tick once a
    caller wires `movement_system=` into TickEngine - see that module's
    docstring), so a later `move` call can resolve "where is this agent
    standing right now, in tile terms" without a second source of truth
    (world.tools.state.WorldState's own module docstring, point 2, has
    the full rationale for why this dict exists and why it stores plain
    tuples). An `agent.moved` event from the legacy opaque-jump fallback
    (agent_id + location_id only, no tile keys) leaves agent_tiles
    untouched, exactly as it should - an opaque jump has no tile to
    record.

    Also attaches the utterance log, which used to be a closure in here.
    """

    async def on_agent_event(event: WorldEvent) -> None:
        if event.type != "agent.moved":
            return
        agent_id = event.payload.get("agent_id")
        tile_x = event.payload.get("tile_x")
        tile_y = event.payload.get("tile_y")
        if agent_id is None or tile_x is None or tile_y is None:
            return
        world.agent_tiles[agent_id] = (tile_x, tile_y)

    world.event_bus.subscribe("agent", on_agent_event)


async def build_world(
    genesis_tick: int = 0,
    seed: int | None = None,
    *,
    world_map: WorldMap | None = None,
    ville_dir: Path = DEFAULT_VILLE_DIR,
    caretaker_pm_agent_id: str | None = None,
    charter_fields: dict[str, object] | None = None,
) -> tuple[WorldState, ToolRegistry]:
    """Construct a fresh world with every built-in primitive and the
    meta-tool registered, a genesis charter seeded, all ten farmland
    strips / five ore outcrops / the forest zone stocked, and (task
    "walking") a real MovementSystem for `move` to path agents against.

    `seed` makes `WorldState.rng` (steal's probability rolls) deterministic
    for tests; production/live use leaves it `None` for a real random seed.

    `world_map`/`ville_dir` are both optional: pass `world_map` when a
    caller already has one loaded (module docstring - avoids a second
    disk load and a second, independently-growing WorldMap instance);
    otherwise this loads `ville_dir` itself (defaulting to the real
    Kasukabe assets this repo ships) via load_movement_world_map(), which
    degrades to `world.movement = None` rather than raising when that
    directory is not present.
    """
    event_bus = EventBus()
    locations = LocationIndex()
    locations.attach(event_bus)

    ledger = Ledger(event_sink=lambda event: event_bus.publish(ledger_event_to_world_event(event)))
    # GROW-12: threaded from the genesis file rather than defaulted here.
    # A parameter that stops at the first boundary it meets is a fold
    # that never reaches the storage key.
    charter = await create_genesis_charter_engine(
        genesis_tick,
        caretaker_pm_agent_id=caretaker_pm_agent_id,
        charter_fields=charter_fields,
    )

    world = WorldState(
        ledger=ledger,
        charter=charter,
        event_bus=event_bus,
        locations=locations,
        current_tick=genesis_tick,
        rng=random.Random(seed),
    )
    resources.seed_resource_zones(world)

    # TOOL-09: the utterance log is not a map feature.
    #
    # `attach_utterance_log` used to be called from inside
    # `attach_tile_tracking`, which runs only when the Ville assets are
    # present. Without them `world.utterances` stayed empty and
    # `open_questions`, `awaiting_reply`, `_continuing_conversation`, the
    # convening hint and the control bakery's "answer whoever asked" all
    # went silently dead - no error, no warning, just a town where nobody
    # had ever said anything to anybody. Production has the map, so no run
    # was affected; a CI host without the assets loses every "said to you"
    # mechanism and reports it as three unrelated test failures.
    attach_utterance_log(world)

    resolved_map = world_map if world_map is not None else load_movement_world_map(ville_dir)
    if resolved_map is not None:
        world.movement = MovementSystem(resolved_map)
        attach_tile_tracking(world)

    registry = ToolRegistry()
    for module in PRIMITIVE_MODULES:
        for spec in module.build_specs():
            registry.register(spec)
    registry.freeze_primitives()

    for spec in meta.build_specs(registry):
        registry.register(spec)

    # B2: `set_intention` refuses a `done_when_tool` naming no tool, so it
    # needs the vocabulary. The NAMES, not the registry - WorldState holds
    # no reference to it (see that module's docstring on sibling systems),
    # and a frozenset of strings is not a back door into one.
    world.registry_tool_names = frozenset(registry.tools)

    # ── Prices, where the agent will actually read them ──────────────────
    #
    # cost_disclosure.py's own "Wiring this in" section, followed exactly.
    # Two halves, and the first is the one that matters:
    #
    # 1. Every tool's description gains one sentence saying what it costs.
    #    This is the only place an agent reliably looks - the tool list it is
    #    handed each turn - so a price anywhere else is a price it has to go
    #    and ask for. Acting costs money as of Part A; an agent that cannot
    #    see that is being charged for something nobody told it about.
    # 2. `quote_action_cost` is registered so an agent can price ONE specific
    #    call, params and skill discount included, rather than reading the
    #    minimum off a description.
    #
    # ToolSpec is frozen, so this is a dataclasses.replace per spec.
    world.action_costs = DEFAULT_SCHEDULE
    # ECON-14: every world gets a head-tax debt store, so `pay_tax` has
    # something to pay into no matter who built the world.
    #
    # Created HERE rather than by each composition root because the roots
    # are exactly what went wrong: both made their own on the runner,
    # where no tool handler could reach it, and `repay_head_tax_debt` sat
    # complete and uncalled while residents carried debts they could not
    # settle. A store the roots ADOPT cannot drift from the one the tool
    # writes to; two stores that merely agree can.
    world.head_tax_debts = HeadTaxDebtStore()
    # ECON-06: the same dict the day boundary keeps, so a tool can ask how
    # hungry the resident calling it is.
    world.hunger_by_agent = {}
    world.gap_feedback = GapFeedback()
    # Registered BEFORE the annotation loop, or it would be the one tool in the
    # registry with no price on it - the tool whose entire job is telling
    # agents what things cost.
    registry.register(cost_disclosure.build_action_cost_quote_spec())
    for name, spec in list(registry.tools.items()):
        registry.tools[name] = dataclasses.replace(
            spec,
            description=cost_disclosure.annotate_tool_description(
                spec.description, name, DEFAULT_SCHEDULE
            ),
        )

    # The world holds the registry so a HANDLER can answer "what could I
    # do", not just the caller who built it. `authoring_context` needs the
    # whole vocabulary priced for one resident, and closing over the
    # registry the way `describe_primitives` does would have meant
    # threading it through every authoring handler by hand.
    #
    # Set LAST, after the price annotation above, so the descriptions a
    # resident reads through this reference are the same annotated ones it
    # gets in its tool schemas. Set before return, so no handler can ever
    # observe a world with a half-built registry on it.
    world.tool_registry = registry

    return world, registry
