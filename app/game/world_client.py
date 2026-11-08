"""WorldClient: the one seam between game/ and a running world/ process.

A Protocol (not an ABC) so mock_world.MockWorldClient and this module's
HttpWorldClient are interchangeable with no shared base class - relay.py
and server.py depend only on the Protocol shape, exactly the pattern
broker/bedrock.py's BedrockGateway Protocol uses for
Boto3BedrockGateway/MockBedrockGateway. config.GameSettings.mock_mode
picks which one server.py constructs (see server.py's build_world_client),
mirroring broker/app.py's build_service().

HttpWorldClient IS the production implementation, and as of this pass it
has actually been run against a live world/ process (`docker compose up
world`, real 250-agent genesis population) rather than only an assumed
contract - see each method's docstring for exactly what was curled and
what came back. That mattered: contracts.py's CONTRACT docstring
describes a `GET /api/game/snapshot` / `GET /api/game/agents/{id}`
surface that world/main.py has never implemented. The real, live
world/main.py (verified 2026-07-31 against a booted `world` container)
exposes a different, leaner surface instead:

    GET  /state/summary          -> {"tick": int, "population": int, ...}
    GET  /agents                 -> [{"agent_id", "alive", "age_years",
                                       "is_minor", "location_id",
                                       "health": {...}, "dormancy": {...},
                                       "usd_wallet_cents", "sim_balance"}]
    GET  /agents/{agent_id}      -> same shape, single dict, or 404
    WS   /ws/events               -> {"type","tick","payload","event_id",
                                       "published_at"} - structurally
                                      identical to contracts.WireEvent,
                                      confirmed live (connected a real
                                      websocket, read real frames:
                                      economy.wallet.credited,
                                      agent.spawned, world.tick.advanced,
                                      agent.moved - all matching this
                                      module's / town.js's expectations).

No `GET /places` and no map-JSON route exist yet on world/main.py today
(confirmed: both 404 live). This task's own brief names `/world/state`
and `/places` as paths "a sibling is adding" - _get_world_tick() below
tries `/world/state` first for exactly that reason, falling back to the
route that is actually live today, and get_places() does the equivalent
for `/places`. Every gap below is called out at the exact call site with
what world/ needs to add.

There is a real, structural gap this module cannot paper over: the live
`/agents` / `/agents/{id}` payload above carries no display name, no
bio, no occupation, no tile coordinate, and no free-text current action
- world.wiring.WorldServer.agent_summary() simply does not serialize
those fields yet, even though world/genesis.json's GenesisAgent has a
real `name`/`bio`/`occupation` and world/core/movement.py's `agent.moved`
WireEvent already carries real tile_x/tile_y once an agent has actually
moved once. This module never invents data to fill that gap (an agent's
`display_name` falls back to its real agent_id, never a fabricated
name); it only derives presentation-layer placement (position.py's
sprite_key_for / fallback_tile_for, already built and documented for
exactly this) and a short, honest status string from the real
health/dormancy fields the payload does carry. See _agent_display_name's
own docstring for the one-line fix whoever owns world/main.py needs to
make to retire that fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
import websockets
from pydantic import BaseModel
from websockets.exceptions import WebSocketException

from game.config import STATIC_ROOT, GameSettings
from game.contracts import (
    AgentDetail,
    AgentPosition,
    AgentSummary,
    RelationshipRecord,
    UtteranceRecord,
    Snapshot,
    Wallets,
    WireEvent,
)
from game.position import fallback_tile_for, sprite_key_for

logger = logging.getLogger("game.world_client")

# UI-30. How much of the town's speech one agent card asks for, and how
# much of it survives the filter.
#
# `/world/utterances` has no per-agent parameter, so the client pulls a
# shared tail and selects from it. The tail has to be generous or a quiet
# resident's card comes back empty while the loud ones fill it: at run
# 23's rate the last 400 remarks are most of a run, and the world keeps
# 2000 (`MAX_UTTERANCES_KEPT`), so this asks for a fifth of what is there
# rather than all of it. What is shown is then capped, because an
# inspector panel is a panel and not a transcript - the Talk feed is the
# place to read the whole conversation.
UTTERANCE_TAIL_FETCHED = 400
RECENT_UTTERANCES_SHOWN = 25


# ── places: real genesis geography, not simulated agent state ──────────
#
# DESIGN.md section 8's places panel needs a name/category/tile for every
# named genesis location (temple, government complex, cafe, grocery,
# pharmacy, park, college, offices, houses, farmland, ore, forest).
# world/ has no GET /places yet (confirmed live, see module docstring),
# so HttpWorldClient falls back to this table when that call 404s -
# get_places()'s own docstring covers exactly when that happens and what
# world/ needs to add to retire it.
#
# This is NOT mock data: every name below is a real the_ville sector
# name already established elsewhere in this codebase (mock_world.py's
# own MOCK_LOCATIONS docstring: "the real the_ville sector names ...
# copied as literal strings" from world/map/genesis.py's
# KNOWN_SECTOR_CATEGORIES), and the five Kasukabe-minted zones
# (government/temple/farmland/ore/forest) are placed at the exact tile
# centers world/map/expansion.py's append_kasukabe_genesis_zones()
# actually computes - not eyeballed:
#   - base map is 140x100 (confirmed: the_ville_jan7.json's own
#     "width"/"height"), DEFAULT_GENESIS_STRIP_DEPTH=30, so the minted
#     strip is rows [100, 130) across the full 140-wide map.
#   - split_span(140, 5) divides the width into 5 equal 28-tile bands,
#     assigned in this exact order: government, temple, farmland, ore
#     outcrop, forest (expansion.py's own zone_spans unpacking).
#   - each entry below is that band's center: x = band_start + 14,
#     y = 100 + 15 = 115.
#
# "houses" has no entry: the base Ville map's individual house/apartment
# sectors (KNOWN_SECTOR_CATEGORIES lists several, e.g. "Adam Smith's
# house") have no tile-center data available to game/ (that lives in
# smallville/'s matrix/special_blocks CSVs, which are not part of this
# package's static assets and are not something game/ should be parsing
# on its own - see this module's own docstring on the module boundary).
# Left out rather than guessed at.
class Place(BaseModel):
    """One named, flyable location for the places panel. Deliberately
    defined here (not contracts.py, which this task does not own) -
    server.py's `GET /api/places` is the only thing that serializes it."""

    place_id: str
    name: str
    category: str
    tile_x: int
    tile_y: int


def _place_id(name: str, category: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"{slug}-{category}"


def _place(name: str, category: str, tile_x: int, tile_y: int) -> Place:
    return Place(place_id=_place_id(name, category), name=name, category=category, tile_x=tile_x, tile_y=tile_y)


GENESIS_PLACES: tuple[Place, ...] = (
    _place("Kasukabe Temple", "temple", 42, 115),
    _place("Kasukabe Government Complex", "government", 14, 115),
    _place("Hobbs Cafe", "cafe", 106, 71),
    _place("The Willows Market and Pharmacy", "grocery", 66, 47),
    _place("The Willows Market and Pharmacy", "pharmacy", 66, 47),
    _place("Johnson Park", "park", 141, 27),
    _place("Oak Hill College", "college", 90, 15),
    _place("Harvey Oak Supply Store", "offices", 22, 117),
    _place("Kasukabe Farmland", "farmland", 70, 115),
    _place("Kasukabe Ore Outcrop", "ore", 98, 115),
    _place("Kasukabe Forest", "forest", 126, 115),
)

# ── spend / burn rate for the HUD ────────────────────────────────────
#
# DESIGN.md section 3's $1000 invariant (wallets + treasury + burned ==
# 100000 cents) plus section 8's HUD requirement ("live spend and burn
# rate"). world/'s admin console exposes a richer `/admin/spend/status`,
# but that is operator-token-gated (world/admin_api.py) - game/ is
# DESIGN.md's "unauthenticated live town view" and must never carry that
# token. Two PUBLIC, unauthenticated routes already give the same number
# a different way (both confirmed live against a real booted world/
# container):
#   GET /ledger/invariant          -> {"expected_cents": 100000, ...}
#   GET /ledger/balance/{account}  -> {"usd_cents": ...} for ANY account,
#                                      including world.ledger.money.
#                                      BURNED_ACCOUNT_ID = "system:burned"
#                                      (DESIGN.md section 3's "burned"
#                                      leg of the invariant).
# Copied literal (not imported - see module docstring on the module
# boundary), matching world/ledger/money.py's BURNED_ACCOUNT_ID exactly.
BURNED_ACCOUNT_ID = "system:burned"


class SpendStatus(BaseModel):
    """Cumulative spend snapshot for the HUD. Burn RATE is a derivative
    of this over time - deliberately computed client-side (static/js/
    main.js polls this route and diffs consecutive samples), not here,
    since "rate" needs wall-clock sampling history a stateless GET can't
    carry."""

    burned_usd_cents: int | None
    total_usd_cents: int | None
    available: bool


GENESIS_MAP_PATH = STATIC_ROOT / "assets" / "the_ville" / "visuals" / "the_ville_jan7.json"


def _load_genesis_map_json(path: Path = GENESIS_MAP_PATH) -> dict:
    """world/ has no live map-serving HTTP route yet - confirmed live:
    GET /map, /api/map, and /world/map all 404 against a real booted
    world/ container. world/admin_api.py's land-petition-approval path
    only ever publishes the petition's metadata over `world.land.
    expanded` (world/map/service.py's MapService.expand_land emits
    `expansion.model_dump()`, never the grown Tiled JSON itself), so
    there is genuinely nowhere to fetch a live, possibly-grown map from
    yet. This is the same real genesis Tiled JSON mock_world.py serves -
    actual base-map geometry lifted from smallville/, not fabricated -
    so a live deploy at least draws the correct starting town; it will
    just not pick up a land expansion until world/ ships a map route.
    One-line ask for whoever owns world/main.py: add
    `GET /map -> MapService.tiled` (already plain JSON per that class's
    own snapshot() method)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── presentation-layer fallbacks for what the real /agents payload
#    does not carry yet (see module docstring) ──────────────────────────


def _agent_display_name(agent_id: str) -> str:
    """world/'s real /agents and /agents/{id} (verified live) return only
    `agent_id` ("a0039"), never a name, even though world/genesis.json's
    GenesisAgent.name ("Ezra Lund") exists - world.wiring.WorldServer.
    agent_summary() simply never serializes it. Falling back to the
    literal agent_id (not a fabricated human name) is the deliberate
    choice: inventing "Ezra Lund" for an agent this client cannot
    actually verify the name of would be exactly the silent-mock-data
    failure mode this task exists to eliminate. One-line ask for
    whoever owns world/main.py: have WorldServer.agent_summary() include
    `name`/`bio`/`occupation` off the same GenesisAgent record it already
    holds, so this fallback can retire."""
    return agent_id


def _current_action_from_health(dormancy: dict[str, Any], health: dict[str, Any]) -> str | None:
    """world/'s real agent payload has no free-text activity/plan field
    reachable over HTTP yet. Synthesizing a short status word from the
    two structured fields it DOES send (dormancy/health - both real,
    both confirmed live) beats leaving the inspector blank, and unlike
    _agent_display_name above there is nothing to fabricate here: every
    word below is a direct, honest read of a real boolean/enum."""
    if dormancy.get("is_dormant"):
        return "dormant (cognitive poverty)"
    ailment = (health or {}).get("current_ailment")
    if ailment:
        kind = ailment.get("kind") or "sick"
        return f"unwell ({kind})"
    return None


def _tile_for_location(location_id: str | None) -> tuple[int | None, int | None]:
    """world/'s real payload never carries tile_x/tile_y on the agent
    roster (only on live `agent.moved` WireEvents, once an agent has
    actually taken a step - confirmed live, see module docstring).
    position.fallback_tile_for() is this package's own, already-built,
    already-documented answer to exactly this gap - see position.py's
    module docstring point 3. Not used for lack of a better idea; used
    because it is the established, tested fallback."""
    if not location_id:
        return None, None
    return fallback_tile_for(location_id)


def _agent_summary_from_world(raw: dict[str, Any]) -> AgentSummary:
    agent_id = raw["agent_id"]
    location_id = raw.get("location_id")
    tile_x, tile_y = _tile_for_location(location_id)
    dormancy = raw.get("dormancy") or {}
    health = raw.get("health") or {}
    return AgentSummary(
        agent_id=agent_id,
        display_name=raw.get("name") or _agent_display_name(agent_id),
        sprite_key=sprite_key_for(agent_id),
        position=AgentPosition(location_id=location_id, tile_x=tile_x, tile_y=tile_y),
        current_action=_current_action_from_health(dormancy, health),
        current_utterance=None,  # world/'s HTTP API exposes no utterances yet
        is_alive=bool(raw.get("alive", True)),
        is_dormant=bool(dormancy.get("is_dormant", False)),
        # Part E: skills and the workflow step ride the SAME /agents row as
        # position and money, so the viewer never has to reconcile two polls
        # taken a tick apart. `or []` because a world server built before
        # these fields existed simply omits them - an older world degrades to
        # "no skills known", never to a raise that drops the whole row.
        skills=raw.get("skills") or [],
        following=raw.get("following") or None,
        age_years=raw.get("age_years"),
        occupation=raw.get("occupation") or None,
        sex=raw.get("sex") or None,
        # UI-04. Present on the row all along and dropped here, exactly as
        # UI-12's bio was. `None` when the world did not send them, so the
        # viewer can tell "the world says nothing" from "the world says
        # nothing is there" - only the first may fall back to the model.
        wallets=(
            Wallets(
                usd_wallet_cents=max(0, int(raw.get("usd_wallet_cents") or 0)),
                sim_balance=max(0, int(raw.get("sim_balance") or 0)),
            )
            if raw.get("sim_balance") is not None
            or raw.get("usd_wallet_cents") is not None
            else None
        ),
    )


def _agent_detail_from_world(
    raw: dict[str, Any],
    utterance_rows: list[dict[str, Any]] | None = None,
) -> AgentDetail:
    agent_id = raw["agent_id"]
    location_id = raw.get("location_id")
    tile_x, tile_y = _tile_for_location(location_id)
    dormancy = raw.get("dormancy") or {}
    health = raw.get("health") or {}
    return AgentDetail(
        agent_id=agent_id,
        display_name=raw.get("name") or _agent_display_name(agent_id),
        sprite_key=sprite_key_for(agent_id),
        # UI-12. These were hard-coded empty, with a comment saying the
        # world exposed no bio "yet". It has: `world/agents_api.py`'s
        # `/agents/{id}` serves bio, personality_traits, habits, spouse_id,
        # parent_ids, children_ids, marital_status, skills and co_located,
        # and every one of them was being dropped on the floor here - so
        # the agent inspector, which DESIGN section 8 defines as "click any
        # agent -> live conversation, memories, wallet, relationships",
        # showed a blank card for a resident the world could describe in
        # full.
        bio=str(raw.get("bio") or ""),
        occupation=raw.get("occupation"),
        age_years=raw.get("age_years"),
        wallets=Wallets(
            usd_wallet_cents=max(0, int(raw.get("usd_wallet_cents", 0) or 0)),
            sim_balance=max(0, int(raw.get("sim_balance", 0) or 0)),
        ),
        position=AgentPosition(location_id=location_id, tile_x=tile_x, tile_y=tile_y),
        current_action=_current_action_from_health(dormancy, health),
        is_alive=bool(raw.get("alive", True)),
        is_dormant=bool(dormancy.get("is_dormant", False)),
        # UI-30. Was hard-coded `[]`, so the one panel DESIGN section 8
        # describes as "click any agent -> live conversation" showed no
        # conversation at all, for a town that said 229 things in run 23
        # and served every one of them at `/world/utterances`.
        recent_utterances=_utterances_for(agent_id, utterance_rows),
        # UI-12 / UI-30: kin, from the fields the world already sends.
        #
        # `RelationshipRecord` existed and was always empty from the real
        # client, which is half of UI-30's "documents a contract nobody
        # implements". The social graph proper lives in each engine's own
        # brain.sqlite and is a separate finding (EVAL-23); what the world
        # can answer today is who somebody is married to and who they are
        # related to, and that is worth more than an empty list.
        relationships=_kin_from_world(raw),
    )


def _utterances_for(
    agent_id: str,
    rows: list[dict[str, Any]] | None,
) -> list[UtteranceRecord]:
    """The tail of what this resident was part of, newest last.

    UI-30. Involvement is speaker OR listener OR named in
    `addressed_agent_ids`, and that third one is the whole point: run 23
    carried more than one addressee on 172 of its 229 utterances, so a
    reader that checked only `listener_agent_id` would drop the second and
    third person out of most conversations in the town. That narrow read
    is the same defect CORE-15 fixed in perception and EVAL-11 fixed in
    the metrics - four separate readers made it, which is why this one is
    written out rather than inlined.

    Rows the world server has not sent (an older world with no
    `/world/utterances`, or a call that failed) are an empty list, not an
    error: a missing Talk feed must not blank an agent card that is
    otherwise complete.
    """
    if not rows:
        return []
    mine: list[UtteranceRecord] = []
    for row in rows:
        speaker = row.get("speaker_agent_id")
        named = {str(x) for x in (row.get("addressed_agent_ids") or []) if x}
        listener = row.get("listener_agent_id")
        if listener:
            named.add(str(listener))
        if speaker != agent_id and agent_id not in named:
            continue
        try:
            mine.append(
                UtteranceRecord(
                    speaker_agent_id=str(speaker),
                    speaker_name=str(row.get("speaker_name") or speaker or ""),
                    listener_agent_id=(str(listener) if listener else None),
                    listener_name=(str(row["listener_name"]) if row.get("listener_name") else None),
                    content=str(row.get("content") or ""),
                    tick=int(row.get("tick") or 0),
                    is_public=bool(row.get("is_public", True)),
                )
            )
        except Exception:
            # One malformed row must not cost a reader the other 199 -
            # the same rule `get_snapshot` applies to one bad /agents entry.
            logger.warning("game.world_client: skipping one malformed utterance row: %r", row)
    return mine[-RECENT_UTTERANCES_SHOWN:]


def _kin_from_world(raw: dict[str, Any]) -> list[RelationshipRecord]:
    """Spouse, parents and children, as the inspector's relationship rows.

    UI-12. Named relations only - this is not the sentiment/trust social
    graph, which no HTTP route serves (EVAL-23). Labelled so a reader can
    tell the two apart rather than mistaking kinship for affinity.
    """
    kin: list[RelationshipRecord] = []
    spouse = raw.get("spouse_id")
    if spouse:
        kin.append(RelationshipRecord(other_agent_id=spouse, relationship_type="spouse"))
    for parent in (raw.get("parent_ids") or []):
        kin.append(RelationshipRecord(other_agent_id=parent, relationship_type="parent"))
    for child in (raw.get("children_ids") or []):
        kin.append(RelationshipRecord(other_agent_id=child, relationship_type="child"))
    return kin


@runtime_checkable
class WorldClient(Protocol):
    """What relay.py and server.py need from "the thing that knows about
    world state" - satisfied by HttpWorldClient (real) and
    mock_world.MockWorldClient (default/test)."""

    async def get_snapshot(self) -> Snapshot: ...

    async def get_agent_detail(self, agent_id: str) -> AgentDetail | None: ...

    async def get_places(self) -> list[Place]: ...

    async def get_workshop(self) -> dict[str, Any]: ...

    async def get_council(self) -> dict[str, Any]: ...

    async def get_government(self) -> dict[str, Any]: ...

    async def get_election(self) -> dict[str, Any]: ...

    async def get_talk(self) -> dict[str, Any]: ...

    async def get_conversations(self) -> dict[str, Any]: ...

    async def get_authored(self) -> dict[str, Any]: ...

    async def get_vitals(self) -> dict[str, Any]: ...

    async def get_preflight(self) -> dict[str, Any]: ...

    async def get_activity(self) -> dict[str, Any]: ...

    async def get_spend_status(self) -> SpendStatus: ...

    async def get_provenance(self) -> dict[str, str | None]:
        """{"cognition": ..., "inference": ...} - see
        HttpWorldClient.get_provenance. Drives the viewer's Real/Mock flag."""
        ...

    def stream_events(self) -> AsyncIterator[WireEvent]:
        """An async generator that yields WireEvents indefinitely,
        reconnecting on its own if the underlying transport drops (see
        HttpWorldClient.stream_events' reconnect loop). Callers
        (relay.py's ConnectionHub) just iterate it; they never see a
        transport-level exception unless the client gives up for good.
        Also yields synthetic `world.connection.lost` /
        `world.connection.restored` WireEvents around a real drop/
        recovery - see HttpWorldClient.stream_events' docstring - so a
        connected browser tab can show an explicit banner instead of
        just silently going stale."""
        ...

    async def close(self) -> None: ...


class WorldClientError(RuntimeError):
    """Raised when world/ cannot be reached at all (connection refused,
    DNS failure, timeout) or when it answered but this module cannot
    make sense of the response (non-200 on a route confirmed to exist,
    or a 200 body that fails validation). Both are "the world is not
    usable right now" from server.py's routes' point of view (both map
    to a 502 - see server.py), but are logged with enough detail to
    tell "world/ is down" from "world/ answered but broke the contract"
    apart in the logs."""


class HttpWorldClient:
    """Real WorldClient: httpx for the REST calls, `websockets` for the
    live event stream. Constructed once per game server process and
    reused - httpx.AsyncClient pools connections, and stream_events()
    holds at most one live socket at a time.
    """

    def __init__(self, settings: GameSettings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.world_base_url, timeout=settings.world_http_timeout_s
        )
        self.closed = False
        self._map_json: dict | None = None
        self._warned_no_places_endpoint = False

    async def _get(self, path: str) -> httpx.Response:
        try:
            return await self.http.get(path)
        except httpx.HTTPError as exc:
            raise WorldClientError(
                f"world/ is unreachable at {self.settings.world_base_url}{path}: {exc}"
            ) from exc

    def _genesis_map_json(self) -> dict:
        if self._map_json is None:
            self._map_json = _load_genesis_map_json()
        return self._map_json

    async def get_provenance(self) -> dict[str, str | None]:
        """How real is this world? Two independent answers, one GET.

        GET /health -> {"cognition": ..., "inference": ...}

          cognition - "engine" (real sim_main subprocesses) | "mock"
                      (MockAgentGateway) | "none" (a world running no
                      engines at all, e.g. world/main.py)
          inference - "bedrock" (real completions) | "mock" (canned text)

        Both are needed, because they are independent. A run can have real
        engine processes, talking to a real world server, being really
        charged per call - and receiving the literal string "mock
        response" every turn. cognition alone would call that real.

        None for either means the world did not say. That is "cannot say",
        NOT "mock": claiming mock for a silent world would show a
        genuinely thinking town as fake.
        """
        blank: dict[str, str | None] = {"cognition": None, "inference": None}
        try:
            resp = await self._get("/health")
            if resp.status_code != 200:
                return blank
            body = resp.json()
        except (WorldClientError, ValueError, AttributeError):
            return blank
        if not isinstance(body, dict):
            return blank
        return {
            key: (body.get(key) if isinstance(body.get(key), str) else None)
            for key in ("cognition", "inference")
        }

    async def _get_world_tick(self) -> int:
        """This task's own brief names `GET /world/state` as the route to
        point at; the real world/main.py shipped as of this pass
        (verified live) only has `GET /state/summary` under that name -
        see module docstring. Tries the named path first so this client
        picks up a rename for free the moment the sibling ships it,
        falls back to the route actually live today so nothing here
        silently invents a tick."""
        last_404_paths: list[str] = []
        for path in ("/world/state", "/state/summary"):
            response = await self._get(path)
            if response.status_code == 404:
                last_404_paths.append(path)
                continue
            if response.status_code != 200:
                raise WorldClientError(
                    f"world/ GET {path} returned {response.status_code}: {response.text[:500]}"
                )
            try:
                return int(response.json()["tick"])
            except Exception as exc:
                raise WorldClientError(
                    f"world/ GET {path} returned a body with no usable integer 'tick' field: {exc}"
                ) from exc
        raise WorldClientError(
            f"world/ at {self.settings.world_base_url} exposes neither "
            f"{' nor '.join(last_404_paths)} - cannot resolve the current tick"
        )

    async def get_snapshot(self) -> Snapshot:
        tick = await self._get_world_tick()
        response = await self._get("/agents")
        if response.status_code != 200:
            raise WorldClientError(
                f"world/ GET /agents returned {response.status_code}: {response.text[:500]}"
            )
        try:
            raw_agents = response.json()
        except Exception as exc:
            raise WorldClientError(f"world/ GET /agents returned invalid JSON: {exc}") from exc

        agents: list[AgentSummary] = []
        for raw in raw_agents:
            try:
                agents.append(_agent_summary_from_world(raw))
            except Exception:
                logger.warning("game.world_client: skipping one malformed /agents entry: %r", raw)
        return Snapshot(tick=tick, map_tiled_json=self._genesis_map_json(), agents=agents)

    async def get_agent_detail(self, agent_id: str) -> AgentDetail | None:
        response = await self._get(f"/agents/{agent_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise WorldClientError(
                f"world/ GET /agents/{agent_id} returned {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            raw = response.json()
        except Exception as exc:
            raise WorldClientError(
                f"world/ GET /agents/{agent_id} returned invalid JSON: {exc}"
            ) from exc
        try:
            return _agent_detail_from_world(raw, await self._recent_utterances())
        except Exception as exc:
            raise WorldClientError(
                f"world/ GET /agents/{agent_id} returned a body this client could not "
                f"interpret as an agent record: {exc}"
            ) from exc

    async def _recent_utterances(self) -> list[dict[str, Any]]:
        """The town's recent speech, for one agent card. Never raises.

        UI-30. A second GET per inspector click, which is a click, not a
        poll - `get_snapshot` runs on the 400 ms loop and is deliberately
        left alone. `/world/utterances` has no per-agent filter, so this
        pulls the shared tail and `_utterances_for` selects from it.

        Swallowing the failure is the deliberate part: an agent card that
        can show a name, a wallet, a position and kin should show them
        even when the Talk feed is unreachable. Raising here would trade
        a missing panel for a missing card.
        """
        try:
            response = await self._get(f"/world/utterances?limit={UTTERANCE_TAIL_FETCHED}")
            if response.status_code != 200:
                return []
            return list(response.json().get("utterances") or [])
        except Exception as exc:
            logger.warning("game.world_client: no recent utterances for the agent card: %s", exc)
            return []

    async def _get_json(self, path: str, what: str) -> dict[str, Any]:
        """One GET that must come back as a JSON object, or raise.

        Shared by the two Part E views because they fail the same way and
        should say so the same way: a non-200 from world/ is a broken
        upstream, not an empty town, and the caller must be able to tell
        those apart.
        """
        response = await self._get(path)
        if response.status_code != 200:
            raise WorldClientError(
                f"world/ GET {path} returned {response.status_code}: {response.text[:500]}"
            )
        try:
            return response.json()
        except Exception as exc:
            raise WorldClientError(f"world/ GET {path} returned invalid JSON: {exc}") from exc

    async def get_workshop(self) -> dict[str, Any]:
        """Part E.3: what the town has invented and authored."""
        return await self._get_json("/workshop", "workshop")

    async def get_council(self) -> dict[str, Any]:
        """Part E.4: governmental workflows, by where they stand."""
        return await self._get_json("/council", "council")

    async def get_government(self) -> dict[str, Any]:
        """Who actually holds the seats.

        UI-04. `world/governance_api.py:758` has served this the whole time -
        prime_minister, ministers, terms_served, off the charter store. The
        township viewer never asked, and filled the five seats itself by
        sorting a hash of the agent ids, so a run's map put sashes on five
        residents the world had never elected and the Government book named
        a Prime Minister who did not exist.
        """
        return await self._get_json("/governance/government", "government")

    async def get_election(self) -> dict[str, Any]:
        """The town's election, as the map and the Election book show it.

        `world/governance_api.py` has served `/voting/elections` on the
        embedded host all along; the township page never asked, so a run
        could hold a whole election - candidates, a campaign, a vote, a
        tally - and the viewer's only sign of it was sashes appearing on the
        winners afterwards. Every election (newest first) plus the detail of
        the one that matters now: the open one, or else the latest.
        Candidate names and running totals only - who voted for whom stays
        off this page, as it stays off the town's own event feed.
        """
        elections = await self._get_json_list("/voting/elections", "elections")
        current = next((e for e in elections if e.get("phase") != "tallied"),
                       elections[0] if elections else None)
        # The newest COUNTED election, separately: the next one is scheduled
        # the moment a vote is counted, so "the open one" stops being the one
        # with the result exactly when the result is what everybody wants.
        latest = next((e for e in elections if e.get("phase") == "tallied"), None)

        async def detail_of(summary):
            if summary is None:
                return None
            body = await self._get_json(
                f"/voting/elections/{summary['election_id']}", "election")
            body.pop("ballots", None)
            return body

        detail = await detail_of(current)
        result = (detail if latest is not None and current is not None
                  and latest["election_id"] == current["election_id"]
                  else await detail_of(latest))
        state = await self._get_json("/world/state", "state")
        return {"elections": elections, "current": detail, "latest_result": result,
                "tick": state.get("tick")}

    async def _get_json_list(self, path: str, what: str) -> list[Any]:
        response = await self._get(path)
        if response.status_code != 200:
            raise WorldClientError(
                f"world/ GET {path} returned {response.status_code}: {response.text[:500]}"
            )
        try:
            body = response.json()
        except Exception as exc:
            raise WorldClientError(f"world/ GET {path} returned invalid JSON: {exc}") from exc
        return body if isinstance(body, list) else []

    async def get_talk(self) -> dict[str, Any]:
        """Agent-to-agent speech, newest last. `say` emits an event only the
        trace store consumed, so a live conversation had no readable surface
        until this route existed."""
        return await self._get_json("/world/utterances?limit=120", "talk")

    async def get_conversations(self) -> dict[str, Any]:
        """The town's conversations with their rosters - who is IN each one,
        not only who has spoken in it. What the group-chat panel reads."""
        return await self._get_json("/world/conversations?limit=40", "conversations")

    async def get_authored(self) -> dict[str, Any]:
        """What the town invented with `toolify` and wrote down with
        `skillify`, plus per-agent proficiency for the character cards."""
        return await self._get_json("/world/authored", "authored")

    async def get_vitals(self) -> dict[str, Any]:
        """Health, buildings, food and treasury - the four panels that used to
        be a client-side model, now read off the running world."""
        return await self._get_json("/world/vitals", "vitals")

    async def get_preflight(self) -> dict[str, Any]:
        """The start-of-run checklist, re-answered live.

        The same checks the runner refuses to start on, so a red line in the
        browser names the same problem the terminal would have."""
        return await self._get_json("/world/preflight", "preflight")

    async def get_activity(self) -> dict[str, Any]:
        """What each resident is doing, from their own completed calls.

        The map used to answer "click an agent, see what they are doing" out
        of dormancy and health alone, so a healthy, busy farmer read blank."""
        return await self._get_json("/world/activity", "activity")

    async def get_places(self) -> list[Place]:
        """Tries the real `/places` route this task's brief names first.
        world/main.py has no such route as of this pass (confirmed live:
        404) - on exactly that 404 this falls back to GENESIS_PLACES
        (real static genesis geography, see that table's own docstring
        for why this is not the "silently serve mock data" this task
        forbids) and logs the gap once, not on every poll. Any other
        failure (world/ unreachable, a 5xx, an unparseable 200 body)
        propagates as WorldClientError same as every other method here -
        a places endpoint that exists but is broken is not the same
        situation as one that simply is not built yet."""
        response = await self._get("/places")
        if response.status_code == 404:
            if not self._warned_no_places_endpoint:
                logger.warning(
                    "game.world_client: world/ at %s has no GET /places endpoint yet "
                    "(confirmed: 404) - serving the built-in genesis place catalog "
                    "(%d entries) instead. One-line ask for whoever owns "
                    "world/main.py: add GET /places -> "
                    "[{place_id, name, category, tile_x, tile_y}, ...] off MapService's "
                    "sector/arena tables.",
                    self.settings.world_base_url,
                    len(GENESIS_PLACES),
                )
                self._warned_no_places_endpoint = True
            return list(GENESIS_PLACES)
        if response.status_code != 200:
            raise WorldClientError(
                f"world/ GET /places returned {response.status_code}: {response.text[:500]}"
            )
        try:
            raw_places = response.json()
        except Exception as exc:
            raise WorldClientError(f"world/ GET /places returned invalid JSON: {exc}") from exc

        places: list[Place] = []
        for raw in raw_places:
            try:
                places.append(
                    Place(
                        place_id=str(raw.get("place_id") or raw.get("id") or raw["name"]),
                        name=raw["name"],
                        category=str(raw.get("category") or raw.get("kind") or "other"),
                        tile_x=int(raw["tile_x"]),
                        tile_y=int(raw["tile_y"]),
                    )
                )
            except Exception:
                logger.warning("game.world_client: skipping one malformed /places entry: %r", raw)
        return places

    async def get_spend_status(self) -> SpendStatus:
        """Two public, unauthenticated calls (see SpendStatus's own
        docstring for why not the operator-gated `/admin/spend/status`):
        `/ledger/invariant` for the fixed $1000 total, `/ledger/balance/
        system:burned` for what has actually been spent so far. Either
        call 404ing (an older world/ build with neither route) degrades
        to `available=False` rather than raising - the HUD showing
        "spend: unavailable" is a much smaller failure than the whole
        page treating a missing HUD number as "world is down"."""
        invariant_response = await self._get("/ledger/invariant")
        if invariant_response.status_code == 404:
            return SpendStatus(burned_usd_cents=None, total_usd_cents=None, available=False)
        if invariant_response.status_code != 200:
            raise WorldClientError(
                f"world/ GET /ledger/invariant returned {invariant_response.status_code}: "
                f"{invariant_response.text[:500]}"
            )

        burned_response = await self._get(f"/ledger/balance/{BURNED_ACCOUNT_ID}")
        if burned_response.status_code == 404:
            return SpendStatus(burned_usd_cents=None, total_usd_cents=None, available=False)
        if burned_response.status_code != 200:
            raise WorldClientError(
                f"world/ GET /ledger/balance/{BURNED_ACCOUNT_ID} returned "
                f"{burned_response.status_code}: {burned_response.text[:500]}"
            )

        try:
            total_cents = int(invariant_response.json()["expected_cents"])
            burned_cents = int(burned_response.json()["usd_cents"])
        except Exception as exc:
            raise WorldClientError(
                f"world/'s ledger routes returned a body this client could not read a "
                f"spend figure from: {exc}"
            ) from exc
        return SpendStatus(burned_usd_cents=burned_cents, total_usd_cents=total_cents, available=True)

    async def stream_events(self) -> AsyncIterator[WireEvent]:
        """Connects to world_ws_url and yields one WireEvent per JSON
        frame, forever, reconnecting with a fixed delay
        (world_ws_reconnect_delay_s) on any drop. Only stops if `close()`
        is called from another task, which flips self.closed and makes
        the reconnect loop exit instead of retrying - the shutdown path
        server.py's lifespan uses.

        Also synthesizes `world.connection.lost` / `world.connection.
        restored` WireEvents around a real drop/reconnect - `lost` the
        moment ANY connect attempt fails (including the very first one
        ever, since a page loaded against a dead world/ needs the banner
        just as much as one that drops mid-session), `restored` the next
        time a connect succeeds after a `lost` was announced (never on a
        plain first-ever successful connect - nothing was lost yet, so
        nothing needs to be reported "restored"). This is the "if the
        world is unreachable show an explicit banner" half of this task:
        relay.py fans out whatever this yields to every connected browser
        tab exactly like a real world/ event, so main.js can special-case
        these two types with zero changes to relay.py or the WS wire
        format - both `type` values are namespaced the same as every real
        domain event (app/CONVENTIONS.md's
        `<domain>.<subject>.<past_tense_verb>`)."""
        had_lost = False
        while not self.closed:
            try:
                async with websockets.connect(self.settings.world_ws_url) as socket:
                    logger.info("game.world_client: connected to %s", self.settings.world_ws_url)
                    if had_lost:
                        yield WireEvent(
                            type="world.connection.restored",
                            tick=0,
                            payload={"world_ws_url": self.settings.world_ws_url},
                            event_id=uuid.uuid4().hex,
                            published_at=time.time(),
                        )
                        had_lost = False
                    async for raw in socket:
                        try:
                            data = json.loads(raw)
                            event = WireEvent.model_validate(data)
                        except Exception:
                            logger.exception(
                                "game.world_client: dropping unparseable frame from world/ WS"
                            )
                            continue
                        yield event
            except (WebSocketException, OSError) as exc:
                if self.closed:
                    return
                logger.warning(
                    "game.world_client: world/ WS connection lost (%s), retrying in %.1fs",
                    exc,
                    self.settings.world_ws_reconnect_delay_s,
                )
                yield WireEvent(
                    type="world.connection.lost",
                    tick=0,
                    payload={"detail": str(exc), "world_ws_url": self.settings.world_ws_url},
                    event_id=uuid.uuid4().hex,
                    published_at=time.time(),
                )
                had_lost = True
                await asyncio.sleep(self.settings.world_ws_reconnect_delay_s)

    async def close(self) -> None:
        self.closed = True
        await self.http.aclose()
