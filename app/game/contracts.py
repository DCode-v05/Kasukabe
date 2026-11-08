"""The wire contract between game/ and world/ - DESIGN.md section 9: "The
game app is fed by the world server's own WebSocket relay (no external
realtime)."

app/CONVENTIONS.md's module-boundary section says game/ "reads live state
from Supabase Realtime, not by polling world's internals" - that line
predates DESIGN.md's 2026-07-30 architecture diagram, which puts a
world-owned WebSocket relay between world/ and game/ instead. Per
CONVENTIONS.md's own header ("if this file and DESIGN.md ever disagree,
DESIGN.md wins and this file is wrong until fixed"), this module follows
DESIGN.md: game/ never touches Supabase, it talks to world/ over plain
HTTP (bootstrap snapshot, agent detail) and a WebSocket (live events)
exposed by the world server process.

None of this is imported from world/ - see module boundary in
app/CONVENTIONS.md ("no package imports another package's internals
across this diagram") and game/README.md (game/ is presentation only,
possibly a separately deployed process). Every model below is this
package's OWN idea of the wire shape, deliberately kept structurally
compatible with world's actual pydantic models (world.core.events.
WorldEvent, world.life.models.GenesisAgent/AgentLifeRecord, world.ledger.
models.Account/Transaction) and with the Supabase column names in
supabase/migrations/*.sql (agents, utterances, relationships, buildings),
so a real world/ HTTP API implementing this contract is a thin
serialization layer over data it already has, not a redesign.

CONTRACT - the routes world/ actually serves, and which model each one
fills. UI-30: this block used to describe an `/api/game/*` surface that
was "expected once world/'s HTTP API is built out past today's /health
placeholder". No such route was ever built, under that name or any other,
and world/ is long past a /health placeholder - run_genesis.py's embedded
app serves the list below and has for many runs. A contract describing a
surface nobody implements is worse than no contract: it sent a reader
looking for `/api/game/snapshot` in a world that answers `/agents`.

  GET  {world_base_url}/world/state, falling back to /state/summary
      -> the current tick. Two names because the brief specified one and
         the world shipped the other; world_client._get_world_tick tries
         both so a rename costs nothing.
  GET  {world_base_url}/agents
      -> one AgentSummary per living resident: position, wallets,
         dormancy, skills, occupation, age, sex.
  GET  {world_base_url}/agents/{agent_id}
      -> AgentDetail: bio, occupation, wallets, current action, and the
         kin (spouse/parents/children) that fill RelationshipRecord.
  GET  {world_base_url}/world/utterances?limit=N
      -> the town's recent speech, which fills UtteranceRecord both for
         the Talk feed and, filtered to one resident, for that agent's
         recent_utterances (DESIGN.md section 8, "click any agent ->
         live conversation, memories, wallet, relationships").
  GET  {world_base_url}/places
      -> Place rows; falls back to this module's own genesis table when
         the world 404s it, see world_client.get_places.
  GET  {world_base_url}/world/{vitals,activity,preflight,authored,
       conversations}, /workshop, /council
      -> the Part E panels, passed through as plain dicts rather than
         modelled here.
  WS   {world_ws_url} (default ws://<world_host>/ws/events)
      -> a stream of WireEvent frames, one per world.core.events.
         WorldEvent published on the in-process EventBus (world/core/
         events.py) - same `type`/`tick`/`payload`/`event_id`/
         `published_at` shape, JSON-encoded one frame per message.

NOT served by any route, and so still empty on the wire:
RelationshipRecord.sentiment and .trust. Kinship is a world fact and is
filled; affinity lives in each engine's own brain.sqlite and no HTTP
surface exposes it (EVAL-23). The fields stay because the supabase
`relationships` table has the columns and a future route would fill them
- but they are named here so a reader does not read "trust: None" as
"this resident trusts nobody".

game/mock_world.py's MockWorldClient satisfies the same WorldClient
Protocol (world_client.py) entirely in-process, so game/tests/ run with
zero network calls and zero dependency on a running world process. It is
no longer the default - GameSettings.mock_mode defaults to False and
`make run-game` talks to a real world (UI-21).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Agent-facing shapes ──────────────────────────────────────────────────
# Field names deliberately mirror supabase/migrations/20260730120100_agents.sql
# (slug, display_name, usd_wallet_cents, sim_balance, occupation, ...) so a
# real world/ HTTP handler can build these by selecting straight off the
# `agents` row plus a location lookup, no field renaming in between.


class Wallets(BaseModel):
    """DESIGN.md section 3's dual currency, read-only here - game/ never
    writes a balance (app/CONVENTIONS.md: "game/'s console ... can read
    balances but never writes them")."""

    usd_wallet_cents: int = Field(ge=0)
    sim_balance: int = Field(ge=0)


class AgentPosition(BaseModel):
    """Where to draw an agent's sprite this tick.

    tile_x/tile_y are the authoritative source when world/ sends them
    (world/map's Sector.tiles already has the geometry to resolve a
    location to a tile - see position.py's module docstring for why that
    resolution belongs in world/, not here). location_id is always sent
    alongside as the human-readable place name for the inspector panel
    and as the input to position.py's client-side fallback placement for
    a world/ build that has not wired coordinates into its events yet.
    """

    location_id: str | None = None
    tile_x: int | None = None
    tile_y: int | None = None


class AgentSummary(BaseModel):
    """One row of the live town view: enough to draw a sprite, a name
    label, and (if `current_utterance` is set) a speech bubble. This is
    the per-agent shape inside Snapshot.agents and inside the
    `agent.moved` / `agent.spoke` WireEvent payloads.
    """

    agent_id: str
    display_name: str
    sprite_key: str
    position: AgentPosition
    current_action: str | None = None
    current_utterance: str | None = None
    is_alive: bool = True
    is_dormant: bool = False
    # Part E. Passed through from world/'s AgentRow untyped: this layer has
    # no opinion about a workflow's shape, and re-declaring the step model
    # here would be a second definition to keep in sync with the one that
    # actually validates it (world/agents_api.py). The viewer reads these.
    skills: list[dict[str, Any]] = Field(default_factory=list)
    following: dict[str, Any] | None = None
    # The world's own answer to "who is this". The viewer carries a built-in
    # 314-resident roster keyed by the same agent ids, but a run against a
    # SUBSET genesis (genesis_14.json and friends) renumbers the town - so
    # a0001 is Magnus Wickham in the world and Cleo Holt in the roster. When
    # a resident is engine-bound, these win: an identity is not a thing the
    # viewer may model beside skills that are real.
    age_years: float | None = None
    occupation: str | None = None
    sex: str | None = None
    # UI-04. The world serves `sim_balance` and `usd_wallet_cents` on the
    # SAME `/agents` row as the position above (world/agents_api.py's
    # AgentRow), and this layer dropped both - so the township viewer ranked
    # the town's wealth off a wage table it invented at build time while the
    # real balance sat unused on the poll it was already making.
    #
    # Optional because a world server predating the fields omits them, and
    # None has to stay distinguishable from zero: a resident the world says
    # holds nothing and a resident the world did not answer for are not the
    # same fact, and only the second may fall back to the model.
    wallets: Wallets | None = None


class UtteranceRecord(BaseModel):
    """Mirrors supabase's `utterances` table columns 1:1 (speaker_agent_id,
    listener_agent_id, content, tick, created_at) - the inspector panel's
    "recent utterances" list (DESIGN.md section 8)."""

    speaker_agent_id: str
    speaker_name: str
    listener_agent_id: str | None = None
    listener_name: str | None = None
    content: str
    tick: int
    is_public: bool = True


class RelationshipRecord(BaseModel):
    """Mirrors supabase's `relationships` table: other_agent_id and
    organization_id are mutually exclusive per that table's own check
    constraint, carried here as two optional fields for the same reason."""

    other_agent_id: str | None = None
    other_agent_name: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    relationship_type: str
    sentiment: float | None = None
    trust: float | None = None
    interaction_count: int = 1


class AgentDetail(BaseModel):
    """Full agent-inspector payload (DESIGN.md section 8: "click any agent
    -> live conversation, memories, wallet, relationships, trajectory").
    research/ memory internals are deliberately out of scope here - the
    inspector shows `bio` (the genesis persona description,
    world.life.models.GenesisAgent.bio) and recent utterances, not raw
    associative-memory nodes, matching the task spec's "bio, wallets,
    current action, recent utterances, relationships" exactly."""

    agent_id: str
    display_name: str
    sprite_key: str
    bio: str
    occupation: str | None = None
    age_years: float | None = None
    wallets: Wallets
    position: AgentPosition
    current_action: str | None = None
    is_alive: bool = True
    is_dormant: bool = False
    recent_utterances: list[UtteranceRecord] = Field(default_factory=list)
    relationships: list[RelationshipRecord] = Field(default_factory=list)


class Snapshot(BaseModel):
    """Bootstrap payload for a browser tab that just opened the game
    (before any live WireEvent has arrived). map_tiled_json is the full
    Tiled JSON (world/map/tiled_json.py's shape - a plain dict here, this
    package does not parse it, only relays it to the browser, which is
    where Phaser actually consumes it).
    """

    tick: int
    map_tiled_json: dict[str, Any]
    agents: list[AgentSummary]


# ── Live event shapes ────────────────────────────────────────────────────


class WireEvent(BaseModel):
    """One frame on the game WebSocket. Structurally identical to
    world.core.events.WorldEvent (type/tick/payload/event_id/published_at)
    by design - see module docstring - but declared independently so this
    package has no import-time dependency on world/.

    `type` follows app/CONVENTIONS.md's event naming scheme
    (`<domain>.<subject>.<past_tense_verb>`); game/relay.py does not
    validate it beyond what pydantic already checks (a full domain
    allowlist would just re-encode world.core.events.KNOWN_DOMAINS a
    second time for no behavioral gain - a domain game/ doesn't recognize
    is simply ignored by the browser's event switch, see static/js/
    town.js), it only forwards whatever world/ published.
    """

    type: str
    tick: int
    payload: dict[str, Any] = Field(default_factory=dict)
    event_id: str
    published_at: float
