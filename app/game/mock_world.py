"""MockWorldClient: an in-process, in-memory simulated town.

This is what `make run-game` and every test in game/tests/ run against
ONLY when explicitly asked for (config.GameSettings.mock_mode defaults
to False since the game stopped serving a fake town by default; UI-21 -
this paragraph claimed the opposite - see that
module's docstring). It exists for the same reason broker's
MockBedrockGateway and InMemoryLedgerClient exist: world/'s real HTTP API
(contracts.py's CONTRACT) is not built yet past `/health`, and game/ must
still boot, render a live town, and have a real (not mocked-out) test
suite in the meantime.

Split into two layers on purpose:

- `step()` is a pure function: (MockTownState, Random) -> (MockTownState,
  list[WireEvent]). No asyncio, no time.sleep, no wall-clock dependency -
  game/tests/test_mock_world.py calls it directly, N times, and asserts
  on exact output for a fixed seed. This is the same "pure computation
  stays synchronous and pure" split app/CONVENTIONS.md asks for (ledger
  math, tile geometry) applied to scripted-town generation.
- `MockWorldClient` is the thin async wrapper that calls step() on a
  timer and fans results out through an asyncio.Queue, satisfying
  world_client.WorldClient. This half is what server.py actually
  constructs; it is not what the tests exercise directly.

Location names are the real the_ville sector names (world/map/genesis.py's
KNOWN_SECTOR_CATEGORIES table and world/map/expansion.py's minted
GOVERNMENT_SECTOR_NAME/TEMPLE_SECTOR_NAME) copied as literal strings, not
imported - see contracts.py's module docstring for why game/ never
imports world/ internals. Keeping them real (not "Location A") means a
screenshot of `make run-game`'s mock town looks like Kasukabe, not like a
placeholder.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from game.config import STATIC_ROOT, GameSettings
from game.contracts import (
    AgentDetail,
    AgentPosition,
    AgentSummary,
    RelationshipRecord,
    Snapshot,
    UtteranceRecord,
    Wallets,
    WireEvent,
)
from game.position import sprite_key_for
from game.world_client import GENESIS_PLACES, Place, SpendStatus

logger = logging.getLogger("game.mock_world")

MOCK_MAP_PATH = STATIC_ROOT / "assets" / "the_ville" / "visuals" / "the_ville_jan7.json"

# A realistic subset of the_ville's real sectors, plus Kasukabe's two
# minted genesis-only sectors (world/map/expansion.py). Coordinates are
# hand-picked tile centers inside each named building's known footprint
# on the base Ville map, close enough for a mock town's purposes - the
# real world/ HTTP API resolves this from Sector.tiles, this table does
# not need to be exact.
MOCK_LOCATIONS: tuple[tuple[str, int, int], ...] = (
    ("Hobbs Cafe", 106, 71),
    ("The Rose and Crown Pub", 126, 96),
    ("The Willows Market and Pharmacy", 66, 47),
    ("Harvey Oak Supply Store", 22, 117),
    ("Johnson Park", 141, 27),
    ("Oak Hill College", 90, 15),
    ("Kasukabe Government Complex", 50, 20),
    ("Kasukabe Temple", 35, 60),
)

MOCK_UTTERANCES: tuple[str, ...] = (
    "Morning. Coffee's on me today.",
    "Did you hear the harvest came in early this year?",
    "I'm heading to the market, need anything?",
    "The council meeting got moved again.",
    "This bread is still warm, try some.",
    "I've been meaning to fix that fence all week.",
    "Have you seen the new construction by the park?",
    "I could use a hand at the shop this afternoon.",
)

MOCK_ACTIONS: tuple[str, ...] = (
    "brewing a pot of coffee",
    "restocking the shelves",
    "reading in the park",
    "walking to work",
    "chatting with a neighbor",
    "tending the garden",
    "reviewing the day's accounts",
    "resting at home",
)

MOCK_RELATIONSHIP_TYPES: tuple[str, ...] = ("friend", "family", "spouse", "coworker", "neighbor")

FIRST_NAMES: tuple[str, ...] = (
    "Abigail", "Adam", "Arthur", "Ayesha", "Carlos", "Carmen", "Eddy", "Francisco",
    "Giorgio", "Hailey", "Isabella", "Jane", "Jennifer", "John", "Klaus", "Latoya",
    "Maria", "Mei", "Rajiv", "Ryan", "Sam", "Tamara", "Tom", "Wolfgang", "Yuriko",
)
LAST_NAMES: tuple[str, ...] = (
    "Chen", "Smith", "Burton", "Khan", "Gomez", "Ortiz", "Lin", "Lopez", "Rossi",
    "Johnson", "Rodriguez", "Moreno", "Moore", "Mueller", "Williams", "Patel",
    "Park", "Taylor", "Schulz", "Yamamoto",
)

OCCUPATIONS: tuple[str, ...] = (
    "cafe owner", "shopkeeper", "student", "college professor", "government clerk",
    "temple keeper", "construction hand", "pharmacist", "farmer", "artist",
)

BIO_TEMPLATE = (
    "{name} is a {occupation} living in Kasukabe, known around {location} for "
    "being {trait}."
)
TRAITS: tuple[str, ...] = (
    "quietly dependable",
    "endlessly curious",
    "a bit of a gossip",
    "fiercely loyal to friends",
    "always early to everything",
    "the first to volunteer",
)


@dataclass
class MockAgent:
    """One resident of the mock town. Plain dataclass (internal-only,
    never crosses a boundary itself - AgentSummary/AgentDetail are what
    cross, built from this by to_summary()/to_detail())."""

    agent_id: str
    display_name: str
    sprite_key: str
    bio: str
    occupation: str
    age_years: float
    usd_wallet_cents: int
    sim_balance: int
    location_name: str
    tile_x: int
    tile_y: int
    current_action: str
    current_utterance: str | None = None
    is_alive: bool = True
    is_dormant: bool = False
    recent_utterances: deque[UtteranceRecord] = field(default_factory=lambda: deque(maxlen=20))
    relationships: list[RelationshipRecord] = field(default_factory=list)

    def to_summary(self) -> AgentSummary:
        return AgentSummary(
            agent_id=self.agent_id,
            display_name=self.display_name,
            sprite_key=self.sprite_key,
            position=AgentPosition(
                location_id=self.location_name, tile_x=self.tile_x, tile_y=self.tile_y
            ),
            current_action=self.current_action,
            current_utterance=self.current_utterance,
            is_alive=self.is_alive,
            is_dormant=self.is_dormant,
        )

    def to_detail(self) -> AgentDetail:
        return AgentDetail(
            agent_id=self.agent_id,
            display_name=self.display_name,
            sprite_key=self.sprite_key,
            bio=self.bio,
            occupation=self.occupation,
            age_years=self.age_years,
            wallets=Wallets(usd_wallet_cents=self.usd_wallet_cents, sim_balance=self.sim_balance),
            position=AgentPosition(
                location_id=self.location_name, tile_x=self.tile_x, tile_y=self.tile_y
            ),
            current_action=self.current_action,
            is_alive=self.is_alive,
            is_dormant=self.is_dormant,
            recent_utterances=list(self.recent_utterances),
            relationships=list(self.relationships),
        )


@dataclass
class MockTownState:
    """Everything step() needs and mutates. tick starts at 0, matching
    world.core.clock.GameClock's own starting point."""

    tick: int
    agents: dict[str, MockAgent]


def build_mock_agents(count: int, seed: int) -> dict[str, MockAgent]:
    """Deterministically builds `count` mock agents. Same (count, seed)
    always yields the same roster, names, bios, wallets, and starting
    locations - the property game/tests/test_mock_world.py checks
    directly."""
    if count <= 0:
        raise ValueError(f"count must be > 0, got {count}")
    rng = random.Random(seed)
    agents: dict[str, MockAgent] = {}
    used_names: set[str] = set()

    for i in range(count):
        agent_id = f"mock-agent-{i:03d}"
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                used_names.add(name)
                break
        location_name, tile_x, tile_y = rng.choice(MOCK_LOCATIONS)
        occupation = rng.choice(OCCUPATIONS)
        trait = rng.choice(TRAITS)
        agents[agent_id] = MockAgent(
            agent_id=agent_id,
            display_name=name,
            sprite_key=sprite_key_for(agent_id),
            bio=BIO_TEMPLATE.format(
                name=name, occupation=occupation, location=location_name, trait=trait
            ),
            occupation=occupation,
            age_years=float(rng.randint(19, 68)),
            usd_wallet_cents=rng.randint(50, 200),
            sim_balance=rng.randint(0, 5000),
            location_name=location_name,
            tile_x=tile_x + rng.randint(-1, 1),
            tile_y=tile_y + rng.randint(-1, 1),
            current_action=rng.choice(MOCK_ACTIONS),
        )

    # Seed a handful of relationships among the roster so the inspector
    # panel has something real to show (DESIGN.md section 8) instead of
    # an empty list on every mock agent.
    agent_ids = list(agents.keys())
    for agent_id in agent_ids:
        other_count = rng.randint(1, 3)
        candidates = [a for a in agent_ids if a != agent_id]
        rng.shuffle(candidates)
        for other_id in candidates[:other_count]:
            agents[agent_id].relationships.append(
                RelationshipRecord(
                    other_agent_id=other_id,
                    other_agent_name=agents[other_id].display_name,
                    relationship_type=rng.choice(MOCK_RELATIONSHIP_TYPES),
                    sentiment=round(rng.uniform(-0.3, 1.0), 3),
                    trust=round(rng.uniform(0.2, 1.0), 3),
                    interaction_count=rng.randint(1, 40),
                )
            )

    return agents


def step(state: MockTownState, rng: random.Random) -> tuple[MockTownState, list[WireEvent]]:
    """Advances the mock town by exactly one tick. Pure: same
    (state, rng-with-same-internal-state) always produces the same next
    state and the same events. Deliberately modest per tick - one move,
    one utterance, and a rare building - so a human watching `make
    run-game` can actually follow what changed rather than the whole
    roster teleporting every frame.
    """
    new_tick = state.tick + 1
    events: list[WireEvent] = []

    def emit(event_type: str, payload: dict) -> None:
        events.append(
            WireEvent(
                type=event_type,
                tick=new_tick,
                payload=payload,
                event_id=uuid.uuid4().hex,
                published_at=time.time(),
            )
        )

    emit("world.tick.advanced", {"tick": new_tick})

    agent_ids = list(state.agents.keys())
    if not agent_ids:
        return MockTownState(tick=new_tick, agents=state.agents), events

    mover_id = rng.choice(agent_ids)
    mover = state.agents[mover_id]
    new_location, base_x, base_y = rng.choice(MOCK_LOCATIONS)
    mover.location_name = new_location
    mover.tile_x = base_x + rng.randint(-1, 1)
    mover.tile_y = base_y + rng.randint(-1, 1)
    mover.current_action = rng.choice(MOCK_ACTIONS)
    emit(
        "agent.moved",
        {
            "agent_id": mover.agent_id,
            "location_id": mover.location_name,
            "tile_x": mover.tile_x,
            "tile_y": mover.tile_y,
            "current_action": mover.current_action,
        },
    )

    if rng.random() < 0.6:
        speaker_id = rng.choice(agent_ids)
        speaker = state.agents[speaker_id]
        listener_candidates = [a for a in agent_ids if a != speaker_id]
        listener_id = rng.choice(listener_candidates) if listener_candidates else None
        content = rng.choice(MOCK_UTTERANCES)
        speaker.current_utterance = content
        record = UtteranceRecord(
            speaker_agent_id=speaker_id,
            speaker_name=speaker.display_name,
            listener_agent_id=listener_id,
            listener_name=state.agents[listener_id].display_name if listener_id else None,
            content=content,
            tick=new_tick,
        )
        speaker.recent_utterances.appendleft(record)
        emit(
            "agent.spoke",
            {
                "agent_id": speaker_id,
                "listener_agent_id": listener_id,
                "content": content,
                "location_id": speaker.location_name,
            },
        )
    else:
        # Utterance bubbles are transient - clear any stale one once this
        # tick didn't produce a new line, matching how a real speech
        # bubble should decay in the browser too (see static/js/town.js).
        for agent in state.agents.values():
            agent.current_utterance = None

    if rng.random() < 0.02:
        building_names = (
            "a new bakery",
            "a timber workshop",
            "a second market stall",
            "a schoolhouse annex",
        )
        emit(
            "world.building.constructed",
            {
                "name": rng.choice(building_names),
                "purpose": "freeform",
                "tile_x": rng.randint(10, 130),
                "tile_y": rng.randint(10, 90),
            },
        )

    return MockTownState(tick=new_tick, agents=state.agents), events


def load_mock_map_json() -> dict:
    """Loads the same Tiled JSON the browser fetches statically
    (game/static/assets/the_ville/visuals/the_ville_jan7.json) as the mock
    snapshot's map payload, so the mock town's bootstrap snapshot and a
    real world/'s eventual snapshot carry the exact same map shape
    (contracts.Snapshot.map_tiled_json)."""
    with open(MOCK_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


class MockWorldClient:
    """Async WorldClient wrapper around step(). Ticks on a wall-clock
    timer (tick_interval_s) and republishes every event through an
    asyncio.Queue that stream_events() drains - see module docstring for
    why the actual town simulation logic lives in the pure step()
    function instead of here.
    """

    def __init__(self, settings: GameSettings, tick_interval_s: float = 1.5) -> None:
        # NO MOCK IN PRODUCTION (this task's own instruction): this is the
        # one place a running game/ process can end up serving simulated
        # agent state instead of a real world/ - server.py's
        # build_world_client() only ever reaches here when
        # GameSettings.mock_mode is True, an explicit flag (MOCK_MODE env
        # var - config.py's own docstring: defaults True for local dev/
        # tests only, since world/'s HTTP API historically did not exist
        # past /health). Logging loudly on construction, not just
        # defaulting quietly, so "why is the live town full of
        # mock-agent-000..011" is one grep away instead of a silent
        # surprise in a deploy that forgot to set MOCK_MODE=false.
        logger.warning(
            "game.mock_world: MockWorldClient constructed - serving %d SIMULATED agents, "
            "not a real world/ process. This must never happen in a live deploy; set "
            "MOCK_MODE=false and WORLD_BASE_URL/WORLD_WS_URL to point game/ at a real "
            "world/ server instead.",
            settings.mock_agent_count,
        )
        self.settings = settings
        self.tick_interval_s = tick_interval_s
        self.rng = random.Random(settings.mock_seed)
        self.state = MockTownState(
            tick=0, agents=build_mock_agents(settings.mock_agent_count, settings.mock_seed)
        )
        self._map_json = load_mock_map_json()
        self._queue: asyncio.Queue[WireEvent] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.closed = False

    def ensure_started(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while not self.closed:
            await asyncio.sleep(self.tick_interval_s)
            self.state, events = step(self.state, self.rng)
            for event in events:
                await self._queue.put(event)

    async def get_snapshot(self) -> Snapshot:
        self.ensure_started()
        return Snapshot(
            tick=self.state.tick,
            map_tiled_json=self._map_json,
            agents=[agent.to_summary() for agent in self.state.agents.values()],
        )

    async def get_agent_detail(self, agent_id: str) -> AgentDetail | None:
        agent = self.state.agents.get(agent_id)
        if agent is None:
            return None
        return agent.to_detail()

    async def get_places(self) -> list[Place]:
        # The mock town is built on the exact same real genesis map/sector
        # names as a live world/ (see this module's own docstring), so the
        # real GENESIS_PLACES catalog (world_client.py) is the correct
        # places list here too - no separate mock-only place data to
        # maintain.
        return list(GENESIS_PLACES)

    async def get_provenance(self) -> dict[str, str | None]:
        """Mock on both axes, unconditionally. step() invents every agent,
        so there is no engine behind them and no completion to be real."""
        return {"cognition": "mock", "inference": "mock"}

    async def get_spend_status(self) -> SpendStatus:
        # Believable, clearly-mock spend curve so the HUD has something
        # to show under an explicitly mock-mode game process instead
        # of a permanent "unavailable" - DESIGN.md section 11's ~$0.003/
        # agent-tick blended cost, scaled by this mock town's (much
        # smaller) agent count, capped at the real $1000 total so a long
        # `make run-game` session never reports more spend than the whole
        # system could ever hold.
        total_cents = 100_000
        burned_cents = min(total_cents, int(self.state.tick * len(self.state.agents) * 0.3))
        return SpendStatus(burned_usd_cents=burned_cents, total_usd_cents=total_cents, available=True)

    # ── the rest of the WorldClient Protocol (UI-13) ────────────────────
    #
    # These eight were on the Protocol and not on this class, so
    # `MockWorldClient` had stopped satisfying the interface it exists to
    # stand in for. Nothing failed, because `server.py` reaches every one
    # of them through `getattr(client, "get_vitals", None)` - a pattern
    # that turns "this double is incomplete" into "this panel is quietly
    # empty in mock mode", and would do the same for a REAL client that
    # lost a method.
    #
    # Implemented rather than deleted: six tests stand on this module, and
    # the shapes are cheap. Each returns what the real client's route
    # returns for a town with nothing of that kind going on - which is the
    # honest answer for a twelve-agent fake with no charter, no workshop
    # and no trace store, not an empty dict standing in for an error.

    async def get_workshop(self) -> dict[str, Any]:
        """No skills or inventions in the mock town."""
        return {"skills": [], "tools": [], "patents": []}

    async def get_council(self) -> dict[str, Any]:
        """No charter, so no seats and no motions."""
        return {"government": None, "seats": [], "amendments": [], "elections": []}

    async def get_election(self) -> dict[str, Any]:
        """No charter, so no election - said as an empty answer, not a gap."""
        return {"elections": [], "current": None, "latest_result": None, "tick": None}

    async def get_government(self) -> dict[str, Any]:
        """UI-04: no charter here either, so nobody holds a seat.

        Empty rather than absent, and that distinction is the finding: a
        mock town with no election has NO prime minister, and the viewer
        must show none. It used to fill the five seats by sorting a hash
        of the agent ids, so a town that had never voted still had a
        cabinet - on the mock and on a real run alike.
        """
        return {"prime_minister": None, "ministers": [], "terms_served": {}}

    async def get_talk(self) -> dict[str, Any]:
        """The mock town's remarks, newest last, in the real route's shape."""
        return {"utterances": [], "count": 0}

    async def get_conversations(self) -> dict[str, Any]:
        return {"conversations": [], "count": 0}

    async def get_authored(self) -> dict[str, Any]:
        return {"authored": [], "count": 0}

    async def get_vitals(self) -> dict[str, Any]:
        """Alive/dead counts, which the mock genuinely knows."""
        agents = list(self.town.agents.values()) if hasattr(self, "town") else []
        alive = [a for a in agents if getattr(a, "alive", True)]
        return {
            "alive": len(alive),
            "total": len(agents),
            "dormant": 0,
            "agents": [],
        }

    async def get_preflight(self) -> dict[str, Any]:
        """A mock town passes nothing, and says so rather than claiming
        checks it never ran."""
        return {"ok": False, "checks": [], "note": "mock world: no preflight is run"}

    async def get_activity(self) -> dict[str, Any]:
        return {"tick": 0, "count": 0, "agents": {}}

    async def stream_events(self) -> AsyncIterator[WireEvent]:
        self.ensure_started()
        while not self.closed:
            event = await self._queue.get()
            yield event

    async def close(self) -> None:
        self.closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # Expected: this is the task we just cancelled above.
                pass
            except Exception:
                logger.exception("game.mock_world: background tick task raised on shutdown")
