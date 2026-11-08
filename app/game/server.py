"""game/'s FastAPI entrypoint - DESIGN.md section 9 / this task's spec:
"a small FastAPI static server + WebSocket endpoint that relays world
events ... from the world server event bus."

Two API surfaces meet in this one process, and they are deliberately
named differently so they are never confused for each other:

- browser <-> game (what static/js/*.js calls): `GET /api/snapshot`,
  `GET /api/map`, `GET /api/places`, `GET /api/agents/{agent_id}`,
  `WS /ws`, plus the static file mount at `/` for index.html/js/css/the
  lifted Ville+character assets.
- game <-> world (what world_client.HttpWorldClient calls):
  `GET {world_base_url}/agents`, `GET {world_base_url}/agents/{id}`,
  `GET {world_base_url}/world/state` (falling back to the real, live
  `/state/summary` - see world_client.py's module docstring for why),
  `GET {world_base_url}/places` (falling back to a real static genesis
  catalog when world/ has not shipped this route yet), and
  `WS {world_ws_url}` (default `/ws/events`) - see world_client.py's
  module docstring for exactly what was verified live against a real
  booted world/ container, since contracts.py's own CONTRACT docstring
  describes an earlier, never-implemented assumed shape
  (`/api/game/snapshot` etc.) that this module no longer calls.

relay.ConnectionHub is the piece that bridges them: one upstream
WorldClient event stream fanned out to every connected browser tab. This
module only wires FastAPI routes to that hub and to the WorldClient's two
REST calls; no town logic, no map logic, no game-state computation lives
here (app/CONVENTIONS.md: "game/ is presentation only").

`make run-game` (uvicorn game.server:app --port 8400) and pytest's
`from game.server import app` both import the module-level `app` below,
which boots with GameSettings.load_validated()'s default, mock_mode
FALSE (UI-21: this claimed the opposite until the default was inverted
and the docstring was not, so the module's own description of what it
boots was wrong)
- see config.py's docstring for why that default exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import re
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from game.config import STATIC_ROOT, GameSettings
from game.position import township_zone_detail

from game.contracts import AgentDetail, Snapshot
from game.mock_world import MockWorldClient
from game.realsim import RealSimController
from game.relay import ConnectionHub
from game.world_client import HttpWorldClient, Place, SpendStatus, WorldClient, WorldClientError

# Where a live, occupied, unrecognised place draws. township_zone_for
# returns None for a string it cannot classify - correct for a string that
# might not be a place at all - but a location that arrived on a snapshot IS
# a place, and the resident standing in it has to appear on the map.
# "29 of 30 on Bedrock", with no way to see the thirtieth, was this hole.
LAST_RESORT_ZONE = "townsquare"


def _drawable_zone(location_id: str | None) -> tuple[str | None, bool]:
    """(zone, exact) for a location that came off a live snapshot.

    The map loop and the engine-status badge both call this. They used to
    count separately, so the badge could say 29 while the map drew 30.
    """
    zone, exact = township_zone_detail(location_id)
    if zone is None and location_id:
        return LAST_RESORT_ZONE, False
    return zone, exact

# Matches `from "./x.js"` / `import "./x.js"` / `import("./x.js")` with a
# relative specifier only - bare and absolute specifiers are left alone.
MODULE_IMPORT_RE = re.compile(r'(from\s+|import\s*\(?\s*|src=)"(\.{1,2}/[^"?]+\.js)"')

logger = logging.getLogger("game.server")


def build_world_client(settings: GameSettings) -> WorldClient:
    """mock_mode picks MockWorldClient (default - see config.py, local
    dev/tests only) or HttpWorldClient - the real implementation, run
    live against a real booted world/ container for this pass (see
    world_client.py's module docstring for exactly what was curled and
    what came back)."""
    # Real world server, always. MockWorldClient is still constructed by
    # game/tests/*.py directly - what is gone is the product ever
    # choosing it, so a running map cannot be showing invented people.
    if settings.mock_mode:  # tests only; unreachable from the environment
        return MockWorldClient(settings)
    return HttpWorldClient(settings)


def _roster_ids(raw: str) -> set[str]:
    """Every agent id the build-time agents.js actually contains."""
    return set(re.findall(r'\{"id":"([^"]+)"', raw))


# What the viewer needs to draw somebody. Kept beside the builder rather
# than in it so the two cannot drift: game/static/js/township/
# build_characters.py writes the same keys at build time.
#
# UI-27: this used to name `build_agents.py`, a SECOND generator that wrote
# the same `agents.js` from the same genesis file with a different set of
# keys. Whichever ran last won, and `agents.js`'s own header named the other
# one. Deleted 2026-09-05; `build_characters.py` is the only builder.
_OCC_DAY_ZONE = {
    "miner": "oreoutcrop", "farmer": "farmland", "forester": "forest",
    "baker": "bakery", "trader": "marketrow", "artisan": "tradeoffices",
    "student": "college", "day_laborer": "construction",
    "grocery_clerk": "grocery", "barista": "cafe",
}


async def _cast_rows_from_world(client: WorldClient) -> list[dict[str, Any]]:
    """The viewer's cast, built from the world instead of a build artefact.

    Only fields the world genuinely knows. Everything the build-time roster
    carries and the world does not - a formative history, a secret, an
    opinion of a neighbour - is left out rather than invented, because a
    fabricated biography under a real resident's name is the exact mock this
    route was written to remove.
    """
    try:
        snapshot = await client.get_snapshot()
    except Exception:  # noqa: BLE001 - no cast is better than a wrong one
        return []

    # The world's own /agents payload, not game.contracts.AgentDetail.
    #
    # AgentDetail is a narrow view built for the inspector: it carries
    # wallets, position and an action, and drops household, spouse, kin,
    # traits and habits. The roster needs those, and the world serves them -
    # the first draft of this asked AgentDetail for household_id and
    # sim_balance, got neither, and served ten residents penniless and
    # homeless.
    # Per agent, not the list route: /agents omits bio, spouse, parents,
    # children, traits and habits - everything that makes a resident card
    # worth opening - and only /agents/{id} carries them. One request each is
    # affordable for a cast this size and the result is cached below until
    # the cast changes.
    raw_by_id: dict[str, dict[str, Any]] = {}
    fetch = getattr(client, "_get", None)
    if fetch is not None:
        for agent in snapshot.agents:
            try:
                response = await fetch(f"/agents/{agent.agent_id}")
                raw_by_id[agent.agent_id] = response.json()
            except Exception:  # noqa: BLE001 - the row survives without extras
                continue

    rows: list[dict[str, Any]] = []
    for agent in snapshot.agents:
        detail = None
        try:
            detail = await client.get_agent_detail(agent.agent_id)
        except Exception:  # noqa: BLE001 - the roster row survives without it
            detail = None
        extra = raw_by_id.get(agent.agent_id, {})

        occupation = (getattr(detail, "occupation", None) or agent.occupation or "")
        wallets = getattr(detail, "wallets", None)
        sex = (agent.sex or "").lower()
        rows.append({
            "id": agent.agent_id,
            "n": agent.display_name or agent.agent_id,
            # No genesis alias: this IS the run's cast, so the two cannot
            # disagree and there is no second name to show.
            "rn": agent.display_name or agent.agent_id,
            "org": "world",
            "age": int(agent.age_years or 0),
            "sex": "f" if sex.startswith("f") else "m",
            "occ": occupation,
            "br": "working",
            "dz": _OCC_DAY_ZONE.get(occupation, "townsquare"),
            # AgentDetail carries `wallets`, not loose balance fields - the
            # first draft of this guessed at household_id/sim_balance and
            # served every resident penniless and homeless.
            "hh": extra.get("household_id") or "",
            # The world renders the enum as "MaritalStatus.MARRIED"; the
            # viewer wants the word.
            "ms": str(extra.get("marital_status") or "single").rsplit(".", 1)[-1].lower(),
            "sp": extra.get("spouse_id") or "",
            "nk": len(extra.get("children_ids") or []),
            "pn": list(extra.get("parent_ids") or []),
            "bal": getattr(wallets, "sim_balance", 0) or 0,
            "usd": getattr(wallets, "usd_wallet_cents", 0) or 0,
            "trait": (extra.get("personality_traits") or [""])[0],
            "habit": (extra.get("habits") or [""])[0],
            "bio": extra.get("bio") or getattr(detail, "bio", "") or "",
            "tension": "", "fear": "", "ambition": "", "values": "",
            "limit": "", "rel": [], "mem": [],
        })
    return rows


def create_app(
    settings: GameSettings | None = None, world_client: WorldClient | None = None
) -> FastAPI:
    """Build a fully wired game FastAPI app.

    `settings`/`world_client` overrides exist for tests
    (game/tests/*.py construct a MockWorldClient directly, or a fake
    WorldClient double, so a test can assert on exactly what the relay
    forwarded without any wall-clock ticking). Production and
    `make run-game` both call `create_app()` with no arguments.
    """
    resolved_settings = settings or GameSettings.load_validated()
    resolved_world_client = world_client or build_world_client(resolved_settings)
    hub = ConnectionHub(resolved_world_client, resolved_settings.replay_buffer_size)
    # Owns at most one live Bedrock run, started from the map's Real sim
    # button. Per-app, not module-global, so tests get their own.
    realsim = RealSimController()

    def live_world_client():
        """The world EVERY read route should answer from right now.

        A run started from the Real sim button brings up its own world, and
        that one wins for as long as it is up.

        UI-20: this used to be true of the township routes only, and
        `/api/snapshot`, `/api/map`, `/api/places`, `/api/spend` and
        `/api/agents/{id}` kept the client the process launched with. The
        reasoning was "starting a run cannot disturb a page that is not
        watching it", and the effect was the opposite of that: ONE page
        mixing two worlds. The township map drew the real-sim town while
        the agent inspector on the same page answered from the other one,
        so clicking a resident could show a stranger's wallet, or a 404
        for somebody visibly standing there.

        Two worlds is a legitimate thing to want; two worlds inside one
        page with nothing saying which is which is not. So every read
        route now answers from the same place, and `/api/township/live`
        reports `source` so the page can say which world that is.

        Still NOT the WebSocket relay: `ConnectionHub` holds one long-lived
        connection opened at process start, and re-pointing it means
        tearing down every browser socket. That is a real limitation and
        `test_the_relay_is_the_known_exception` pins it as known rather
        than leaving it to be rediscovered.
        """
        return realsim.world_client or resolved_world_client

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.start()
        yield
        await hub.stop()
        # A run outlives this process otherwise: its own process group
        # holding port 8100 and one engine per agent, and it is SPENDING.
        await realsim.stop()
        await resolved_world_client.close()

    app = FastAPI(title="kasukabe-game", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.world_client = resolved_world_client
    app.state.hub = hub
    app.state.realsim = realsim

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "game"}

    @app.get("/api/snapshot", response_model=Snapshot)
    async def snapshot() -> Snapshot:
        try:
            return await live_world_client().get_snapshot()
        except WorldClientError as exc:
            logger.warning("game.server: snapshot fetch from world/ failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # ── UI-08: one upstream fan-out per window, however many ask ───────
    #
    # `/api/township/live` costs `/health` + `/world/state` + `/agents` +
    # `/world/activity` upstream, and the `/agents` row for 20-50
    # residents carries skills and workflow documents. `/api/engine`
    # repeats three of those and adds two broker calls.
    #
    # Both are polled twice over: `township-v2.js` every 2 s and
    # `live-cast.js` every 10 s hit the live feed, and `/api/engine` goes
    # every 2 s. That is ~2.5 requests a second to the game server per
    # OPEN TAB, and roughly 8 upstream requests a second to a world
    # process that is simultaneously serving every engine's tool calls -
    # a world where 10 turns per run were already being discarded at the
    # 120-second dispatch limit.
    #
    # A window shorter than the fastest poller, so the browser's own
    # cadence is unchanged and nothing on screen goes stale, but two
    # pollers and ten tabs collapse into one fetch. Single-flight as well
    # as cached: without the lock, N callers arriving on a cold cache all
    # start their own fan-out, which is exactly the burst this is for.
    # KEYED ON THE WORLD, not on the route alone. The Real-sim button
    # swaps `live_world_client()` mid-process, and a key that ignored that
    # would keep serving the previous town for a whole window - so the map
    # would show the old world for a beat after the run started, which is
    # the bug UI-20 just fixed arriving through a different door. A test
    # caught exactly that.
    LIVE_FEED_TTL_S = 1.0

    class _Shared:
        """One in-flight fetch, one cached answer, per key."""

        def __init__(self) -> None:
            self._entries: dict[str, tuple[float, Any]] = {}
            self._locks: dict[str, asyncio.Lock] = {}

        async def get(self, key: str, produce, ttl: float):
            now = time.monotonic()
            hit = self._entries.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                # Re-check inside the lock: whoever held it may have just
                # filled the cache, and the whole point is that the second
                # caller does not repeat the work.
                hit = self._entries.get(key)
                if hit is not None and time.monotonic() - hit[0] < ttl:
                    return hit[1]
                value = await produce()
                self._entries[key] = (time.monotonic(), value)
                return value

    shared = _Shared()
    app.state.shared_feed = shared

    @app.get("/api/township/live")
    async def township_live() -> JSONResponse:
        """UI-08: shared across every poller and every open tab. See
        `_Shared` above for why - two pollers hit this route and each open
        tab repeats both."""
        return await shared.get(
            f"township-live:{id(live_world_client())}",
            _township_live_uncached,
            LIVE_FEED_TTL_S,
        )

    async def _township_live_uncached() -> JSONResponse:
        """Live agent placement for the township viewer
        (static/township-v2.html), as districts rather than tiles.

        The township draws its own 104x104 town, not the Ville map world/
        paths agents over, so a live engine tile cannot be plotted on it -
        see game/position.py's township_zone_for() for why the named place
        is the honest bridge and why an unmapped location returns None.

        `bound` counts the rows the viewer can actually use. The viewer
        keeps every unbound resident on its built-in daily schedule, so a
        502 here, a mock world whose agent_ids do not match genesis, or an
        empty list are all non-events: the page carries on exactly as it
        does with no world server at all.

        `source` is the provenance of these rows, and the viewer's badge
        turns on it: "mock" rows are fabricated by MockWorldClient and
        describe nothing real, so the page must never present them as a
        live engine however plausible the ids look.
        """
        # Taken from the constructed CLIENT, not settings.mock_mode:
        # create_app(world_client=...) lets a caller inject a client that
        # disagrees with the setting (every game/tests/*.py does exactly
        # that), and in precisely that case the flag would report the
        # opposite of what is being served. The object is the truth; the
        # setting is only how it was chosen.
        active_client = live_world_client()
        source = "mock" if isinstance(active_client, MockWorldClient) else "world"

        # Layer 3: are the agents in that world backed by real engine
        # subprocesses, or by MockAgentGateway? `source` cannot answer it -
        # a perfectly real world server still says nothing about how its
        # agents think - so it is asked separately and reported separately.
        #
        # getattr, not a direct call: WorldClient is a structural Protocol
        # and game/tests/*.py inject minimal doubles that predate this
        # method. A missing method means "cannot say" (None), which the
        # viewer shows as unknown rather than guessing either way.
        get_provenance = getattr(active_client, "get_provenance", None)
        cognition: str | None = None
        inference: str | None = None
        if get_provenance is not None:
            try:
                prov = await get_provenance()
                cognition = prov.get("cognition")
                inference = prov.get("inference")
            except Exception:  # noqa: BLE001 - provenance must never break the feed
                cognition = inference = None

        try:
            snap = await active_client.get_snapshot()
        except WorldClientError as exc:
            logger.info("game.server: township live feed unavailable: %s", exc)
            return JSONResponse(
                content={"connected": False, "source": source, "cognition": cognition,
                         "inference": inference, "tick": None, "bound": 0, "agents": [],
                         # UI-04: same keys on both branches. A missing key
                         # and a null one read identically in JS, and the
                         # viewer's rule turns on "did the world say".
                         "government": None,
                         "detail": str(exc)}
            )
        # What each resident is doing, joined onto the SAME poll as their
        # position - a second call would disagree by a tick and nobody could
        # tell why. A world too old to have the route leaves this empty and
        # the card falls back to the health/dormancy line, as it always did.
        activity: dict[str, Any] = {}
        get_activity = getattr(active_client, "get_activity", None)
        if get_activity is not None:
            try:
                activity = (await get_activity()).get("agents") or {}
            except Exception:  # noqa: BLE001 - the map must not need this
                activity = {}

        # UI-04. Who really holds the five seats. On the SAME poll as the
        # positions for the same reason activity is: the viewer draws a sash
        # on the map and a name in the Government book, and those two must
        # not be a tick apart. A world too old to have the route, or one
        # with no election yet, leaves this None - which the viewer shows as
        # "no government" rather than filling the seats itself.
        government: dict[str, Any] | None = None
        get_government = getattr(active_client, "get_government", None)
        if get_government is not None:
            try:
                government = await get_government()
            except Exception:  # noqa: BLE001 - the map must not need this
                government = None

        rows = []
        for agent in snap.agents:
            zone, exact = _drawable_zone(agent.position.location_id)
            doing = activity.get(agent.agent_id) or {}
            rows.append({
                "id": agent.agent_id,
                "zone": zone,
                # False means "drawn, but the district is a guess" - the card
                # says so rather than asserting a position the town never gave.
                "zone_exact": exact,
                "location_id": agent.position.location_id,
                # The health/dormancy line stays first when it is set:
                # "unwell (fever)" outranks "harvesting wheat" on a card
                # meant to tell you why a resident has stopped.
                "action": agent.current_action or doing.get("doing"),
                # The tail behind that line - what the click opens.
                "doing": doing.get("doing"),
                "doing_tick": doing.get("at_tick"),
                "recent": doing.get("recent") or [],
                "utterance": agent.current_utterance,
                "alive": agent.is_alive,
                "dormant": agent.is_dormant,
                # Part E.1/E.2/E.5, carried on the row the viewer already
                # polls. `following` is what the roster shows as "step 4/7"
                # and what puts the mid-workflow mark on the map; `skills`
                # is the document the Skills tab renders. One poll, one
                # instant - a second call for these would disagree with the
                # position above by a tick and nobody could tell why.
                "skills": agent.skills,
                "following": agent.following,
                # Who the WORLD says this is. A subset genesis renumbers the
                # town, so the viewer's built-in roster can name a different
                # person for the same id - and an engine-bound resident must
                # be identified by the run, not by the model.
                "name": agent.display_name,
                "age_years": agent.age_years,
                "occupation": agent.occupation,
                "sex": agent.sex,
                # UI-04. The money the WORLD holds for this resident. The
                # viewer ranked town wealth off its own build-time wage
                # table while this sat unread on the row - so the "Wealthiest"
                # leaderboard was a property of the wage constants, not of
                # the run. None (not 0) when the world did not say, because
                # only that case may fall back to the model.
                "sim_balance": (
                    agent.wallets.sim_balance if agent.wallets else None
                ),
                "usd_wallet_cents": (
                    agent.wallets.usd_wallet_cents if agent.wallets else None
                ),
            })
        return JSONResponse(content={
            "connected": True,
            "source": source,
            # "engine" = real sim_main subprocesses; "mock" = faked;
            # "none" = a world with no engines; null = the world did not say.
            "cognition": cognition,
            # "bedrock" = real completions; "mock" = canned text; null =
            # not reported. Independent of cognition: real engines fed
            # canned completions look identical from here without it.
            "inference": inference,
            "tick": snap.tick,
            # UI-04: {prime_minister, ministers, terms_served} or None.
            "government": government,
            "bound": sum(1 for r in rows if r["zone"]),
            # How much of the town is executing a written procedure rather
            # than improvising. Watching this fraction rise across a run is
            # the clearest single signal that authored skills are catching on.
            "following": sum(1 for r in rows if r["following"]),
            "agents": rows,
        })

    async def _town_view(fetch: str) -> JSONResponse:
        """Serve one Part E town view, or explain why it is empty.

        Both views degrade the same way and it matters that they do: a town
        that has invented nothing yet and a world server that is unreachable
        look identical if the answer is just an empty list. `available` says
        which one this is, so the panel can print "nothing filed yet" rather
        than silently implying the town is idle when the pipe is broken.
        """
        client = live_world_client()
        method = getattr(client, fetch, None)
        if method is None:
            return JSONResponse(content={
                "available": False,
                "detail": f"this world client cannot serve {fetch.replace('get_', '')}",
            })
        try:
            payload = await method()
        except WorldClientError as exc:
            logger.info("game.server: %s unavailable: %s", fetch, exc)
            return JSONResponse(content={"available": False, "detail": str(exc)})
        payload["available"] = True
        return JSONResponse(content=payload)

    @app.get("/api/town/workshop")
    async def town_workshop() -> JSONResponse:
        """Part E.3: every tool invented and workflow authored, with the
        adoption figures that say whether the town took the idea up."""
        return await _town_view("get_workshop")

    @app.get("/api/town/council")
    async def town_council() -> JSONResponse:
        """Part E.4: governmental workflows only, by where they stand."""
        return await _town_view("get_council")

    @app.get("/api/town/election")
    async def town_election() -> JSONResponse:
        """The election: phase, schedule, candidates, running totals, result.
        What the map's election strip and the Election book read."""
        return await _town_view("get_election")

    @app.get("/api/town/talk")
    async def town_talk() -> JSONResponse:
        """Agent-to-agent speech, newest last. The one thing a live run
        produced that no surface showed."""
        return await _town_view("get_talk")

    @app.get("/api/town/conversations")
    async def town_conversations() -> JSONResponse:
        """Group chats: roster, place, whether still open, and the lines.

        Separate from /api/town/talk, which is a flat tail of speech. A
        conversation is the thing a reader wants - who is round the table,
        and what they are saying to each other."""
        return await _town_view("get_conversations")

    @app.get("/api/town/authored")
    async def town_authored() -> JSONResponse:
        """`toolify` and `skillify` output, plus per-agent proficiency so a
        character card can show what this resident actually knows."""
        return await _town_view("get_authored")

    @app.get("/api/town/vitals")
    async def town_vitals() -> JSONResponse:
        """Health, buildings, food and treasury, from the world."""
        return await _town_view("get_vitals")

    @app.get("/api/town/preflight")
    async def town_preflight() -> JSONResponse:
        """The fifteen-point checklist, re-run against the live world.

        Not cached: it is read precisely when someone suspects something is
        wrong, and a stale answer then is worse than no answer."""
        return await _town_view("get_preflight")

    @app.get("/api/realsim/defaults")
    async def realsim_defaults() -> JSONResponse:
        """What the Real sim dialog should offer, and what it may not exceed.

        The ceiling is arithmetic the browser must not invent: the town holds
        a fixed $1000, so the largest equal purse is $1000/cast. A dialog that
        guesses it lets the operator submit a run that is refused after the
        genesis file has already been written.
        """
        from game.realsim import (
            DEFAULT_AGENTS,
            DEFAULT_BUDGET_CAP_CENTS,
            DEFAULT_UNTIL_SURVIVORS,
            DEFAULT_WALLET_CENTS,
            MAX_AGENTS,
            MIN_AGENTS,
            TOTAL_USD_CENTS,
        )

        return JSONResponse(content={
            "until_survivors": DEFAULT_UNTIL_SURVIVORS,
            "agents": DEFAULT_AGENTS,
            "min_agents": MIN_AGENTS,
            "max_agents": MAX_AGENTS,
            "wallet_cents": DEFAULT_WALLET_CENTS,
            "budget_cap_cents": DEFAULT_BUDGET_CAP_CENTS,
            "town_total_cents": TOTAL_USD_CENTS,
            "status": realsim.status(),
        })

    @app.post("/api/realsim/start")
    async def realsim_start(body: dict) -> JSONResponse:
        """Begin a FRESH run: new genesis, cleared memories, engines up.

        Spends real money, so a refusal is returned as a message the dialog
        can show rather than as a 500 the operator has to read a log to
        understand.
        """
        from game.realsim import (
            DEFAULT_AGENTS,
            DEFAULT_BUDGET_CAP_CENTS,
            DEFAULT_UNTIL_SURVIVORS,
            DEFAULT_WALLET_CENTS,
        )

        try:
            agents = int(body.get("agents", DEFAULT_AGENTS))
            wallet_cents = int(body.get("wallet_cents", DEFAULT_WALLET_CENTS))
            budget_cap_cents = int(body.get("budget_cap_cents", DEFAULT_BUDGET_CAP_CENTS))
            until_survivors = int(body.get("until_survivors", DEFAULT_UNTIL_SURVIVORS))
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={
                "ok": False, "error": "agents, wallet_cents, budget_cap_cents and "
                                      "until_survivors must all be whole numbers."})
        result = await realsim.start(agents, wallet_cents, budget_cap_cents, until_survivors)
        return JSONResponse(status_code=200 if result["ok"] else 400, content=result)

    @app.post("/api/realsim/stop")
    async def realsim_stop() -> JSONResponse:
        """Stop the run and every engine it spawned. Idempotent."""
        return JSONResponse(content=await realsim.stop())

    @app.get("/api/engine")
    async def engine_status() -> JSONResponse:
        """UI-08: shared, like the live feed. This one repeats three of
        that route's upstream calls and adds two broker calls, on its own
        2-second timer."""
        return await shared.get(
            f"engine:{id(live_world_client())}",
            _engine_status_uncached,
            LIVE_FEED_TTL_S,
        )

    async def _engine_status_uncached() -> JSONResponse:
        """One answer to "how much of this town is actually running on
        Bedrock right now", for the viewer's Real sim readout.

        The page could assemble this from /api/township/live plus the
        broker's /admin/lanes and /admin/spend, but then the three numbers
        it shows side by side would come from three polls at three
        different instants, and the one question an operator asks of a live
        run - "are the agents I am paying for the agents I am watching?" -
        would be answered by comparing figures that were never true at the
        same time.

        Every field degrades to a stated unknown rather than a guess. A
        missing broker, a world server that never came up and a world whose
        agents are mock-backed are all normal states with different
        answers, and conflating them is what makes a live run impossible to
        debug.
        """
        import os as _os

        import httpx as _httpx

        out: dict[str, object] = {
            "world": {"connected": False, "cognition": None, "inference": None, "tick": None,
                      "reported": 0, "bound": 0, "alive": 0, "dormant": 0,
                      "acting": 0, "reason": None},
            "broker": {"live": False, "reason": None, "lanes": [],
                       "spent_micros": None, "spent_usd": None, "cap_usd": None,
                       "state": None},
            # COPIED, not imported: game/ is presentation only and does not
            # import broker/ (app/CONVENTIONS.md). Source of truth is
            # broker/config.py's MODEL_ALLOWLIST - if these two drift, the
            # readout names a model nobody is calling. Held by
            # game/tests/test_engine_status.py::test_model_names_match_the_broker.
            "models": {"cognition": "moonshotai.kimi-k2.5",
                       "background": "deepseek.v3.2"},
            # The survival run's own scoreboard, carried here so the panel
            # needs one poll rather than two - and so it keeps reading
            # correctly after the watcher has stopped the run and the world
            # it was counting has gone.
            "realsim": realsim.status(),
        }

        # ── the world: who exists, and is anything thinking behind them ──
        client = live_world_client()
        source = "mock" if isinstance(client, MockWorldClient) else "world"
        out["world"]["source"] = source
        # get_provenance, not get_cognition: this tree returns BOTH axes
        # from one /health call, because cognition alone cannot answer the
        # question this readout exists for. Real engines fed canned
        # completions report cognition="engine" and are not thinking.
        get_provenance = getattr(client, "get_provenance", None)
        if get_provenance is not None:
            try:
                prov = await get_provenance()
                out["world"]["cognition"] = prov.get("cognition")
                out["world"]["inference"] = prov.get("inference")
            except Exception:  # noqa: BLE001 - provenance must never break the feed
                out["world"]["cognition"] = None
                out["world"]["inference"] = None
        try:
            snap = await client.get_snapshot()
        except WorldClientError as exc:
            out["world"]["reason"] = str(exc)[:200]
        else:
            agents = list(snap.agents)
            out["world"].update({
                "connected": True,
                "tick": snap.tick,
                "reported": len(agents),
                # `bound` is the number the viewer can actually place on the
                # township map - the honest denominator for "agents you are
                # watching", which is not the same as "agents that exist".
                "bound": sum(1 for a in agents
                             if _drawable_zone(a.position.location_id)[0]),
                "alive": sum(1 for a in agents if a.is_alive),
                "dormant": sum(1 for a in agents if a.is_dormant),
                # Doing something this tick, as opposed to merely existing.
                "acting": sum(1 for a in agents if a.current_action),
            })

        # ── the broker: which lanes are up, and what has been spent ──────
        base = _os.environ.get("BROKER_BASE_URL", "http://127.0.0.1:8200").rstrip("/")
        try:
            async with _httpx.AsyncClient(timeout=2.0) as client:
                lanes_res, spend_res = await asyncio.gather(
                    client.get(f"{base}/admin/lanes"),
                    client.get(f"{base}/admin/spend"),
                )
                lanes_res.raise_for_status()
                spend_res.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - a missing broker is normal
            out["broker"]["reason"] = f"broker not reachable at {base}: {str(exc)[:120]}"
        else:
            spend = spend_res.json()
            micros = spend.get("spent_micros")
            if micros is None and spend.get("spent_usd") is not None:
                micros = round(float(spend["spent_usd"]) * 1_000_000)
            out["broker"].update({
                "live": True,
                "lanes": lanes_res.json(),
                "spent_micros": micros,
                "spent_usd": spend.get("spent_usd"),
                "cap_usd": spend.get("cap_usd"),
                "state": spend.get("state"),
            })

        return JSONResponse(content=out)

    @app.get("/api/cost")
    async def cost_status() -> JSONResponse:
        """What the run has actually spent, in micro-dollars and in dollars.

        The township HUD has always shown a MODELLED figure - arithmetic the
        page does itself, which is honest for a mock run and useless for a
        real one. This is the real number, read straight off the broker's
        spend governor, which accounts in nano-dollars and never rounds per
        call (broker/pricing.py). A tick costs a fraction of a cent, so
        dollars alone read $0.00 for a long while into a real run; micros are
        what actually moves.

        `live` is the flag the viewer's Real/Mock badge turns on. A broker
        that is not running, or is running in mock mode, is not an error
        here - it is the normal state while the town runs on the mock, and
        the page says so rather than showing a fabricated cost as if it were
        billed.
        """
        import os as _os

        import httpx as _httpx

        base = _os.environ.get("BROKER_BASE_URL", "http://127.0.0.1:8200").rstrip("/")
        try:
            async with _httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{base}/admin/spend")
                res.raise_for_status()
                d = res.json()
        except Exception as exc:  # noqa: BLE001 - a missing broker is normal
            return JSONResponse(content={
                "live": False, "reason": f"broker not reachable at {base}",
                "detail": str(exc)[:200],
                "spent_micros": None, "spent_usd": None, "cap_usd": None,
            })
        micros = d.get("spent_micros")
        if micros is None and d.get("spent_usd") is not None:
            micros = round(float(d["spent_usd"]) * 1_000_000)
        # The RUN's budget, not the broker's ceiling. run-scenario.sh sets the
        # broker $5 above the runner's cap so the end-of-run learning drain has
        # room, so the broker's own cap_usd is $65 on a $60 run - and an
        # operator reading "of $65.00" on the HUD is reading a number nobody
        # chose. RUN_BUDGET_USD is what was actually asked for; without it the
        # broker's figure is still shown, because a wrong label is worse than
        # an unlabelled one.
        run_budget = _os.environ.get("RUN_BUDGET_USD")
        try:
            run_budget = float(run_budget) if run_budget else None
        except ValueError:
            run_budget = None
        return JSONResponse(content={
            "live": True,
            "model": d.get("model"),
            "model_is_exclusive": d.get("model_is_exclusive"),
            "spent_micros": micros,
            "spent_usd": d.get("spent_usd"),
            "cap_usd": run_budget if run_budget else d.get("cap_usd"),
            "broker_cap_usd": d.get("cap_usd"),
            "fraction_used": d.get("fraction_used"),
            "burn_rate_usd_per_hr": d.get("burn_rate_usd_per_hr"),
            "state": d.get("state"),
        })

    @app.get("/api/rules")
    async def village_rules() -> JSONResponse:
        """The town's rulebook, read-only.

        `world/rules/village_rules.json` is the canonical copy; this serves it
        so the viewer can show the rules the town is actually running by. Read
        as a data file by path, never imported - game/ has no import-time
        dependency on world/ (app/CONVENTIONS.md), the same way
        world_client.py already reads the genesis map.

        A rulebook that fails validation is served as an error rather than
        silently omitted: if the town is running on rules that do not hold
        together, that is the single most important thing the page can say.
        """
        import json as _json
        from pathlib import Path as _Path

        rules_path = (
            _Path(__file__).resolve().parents[1] / "world" / "rules" / "village_rules.json"
        )
        if not rules_path.exists():
            return JSONResponse(
                status_code=404, content={"ok": False, "error": "no rulebook found"}
            )
        try:
            data = _json.loads(rules_path.read_text(encoding="utf-8"))
        except _json.JSONDecodeError as exc:
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": f"rulebook is not valid JSON: {exc}"},
            )
        return JSONResponse(content={"ok": True, "path": "world/rules/village_rules.json",
                                     "rules": data})

    @app.get("/api/map")
    async def map_tiled_json() -> JSONResponse:
        """Raw Tiled JSON, not wrapped in a Snapshot envelope - this is
        the URL static/js/town.js's Phaser preload() hands straight to
        `this.load.tilemapTiledJSON("map", ...)`, which expects a Tiled
        map file's exact shape at the URL it is given, not an envelope
        around one. /api/snapshot's `map_tiled_json` field carries the
        same content for callers that want it alongside agent state in
        one round trip (there is currently only one such caller: town.js
        itself, on first load, via fetchSnapshot() - this route exists so
        Phaser's own file loader can also fetch it directly on every
        scene (re)start, including after a `world.land.expanded` reload).
        """
        try:
            snap = await live_world_client().get_snapshot()
        except WorldClientError as exc:
            logger.warning("game.server: map fetch from world/ failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(content=snap.map_tiled_json)

    @app.get("/api/places", response_model=list[Place])
    async def places() -> list[Place]:
        """Every named genesis location for the places panel (DESIGN.md
        section 8) - temple, government complex, cafe, grocery, pharmacy,
        park, college, offices, farmland, ore, forest. Backed by world/'s
        real `GET /places` when that exists, or world_client.py's
        real-static-geography fallback catalog when it does not yet (see
        HttpWorldClient.get_places' docstring) - either way this route
        never fabricates a location, it only ever forwards what
        world_client.py resolved."""
        try:
            return await live_world_client().get_places()
        except WorldClientError as exc:
            logger.warning("game.server: places fetch from world/ failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/spend", response_model=SpendStatus)
    async def spend_status() -> SpendStatus:
        """Cumulative spend for the HUD (DESIGN.md section 8: "live spend
        and burn rate"). `available: false` (not a 502) is the normal
        answer against a world/ build that has not shipped the ledger
        routes this needs yet - see HttpWorldClient.get_spend_status'
        docstring; only a genuinely unreachable world/ raises here."""
        try:
            return await live_world_client().get_spend_status()
        except WorldClientError as exc:
            logger.warning("game.server: spend status fetch from world/ failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/agents/{agent_id}", response_model=AgentDetail)
    async def agent_detail(agent_id: str) -> AgentDetail:
        try:
            detail = await live_world_client().get_agent_detail(agent_id)
        except WorldClientError as exc:
            logger.warning("game.server: agent detail fetch from world/ failed: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if detail is None:
            raise HTTPException(status_code=404, detail=f"unknown agent_id {agent_id!r}")
        return detail

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await hub.register(websocket)
        try:
            while True:
                # The browser never sends anything meaningful over this
                # socket (it is a server-push relay - static/js/ws_client.js
                # never calls send()); this await just blocks until the
                # browser tab closes or the connection otherwise drops,
                # which is exactly the signal this handler needs to clean
                # up and return.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister(websocket)

    # Mounted last and at "/" so every route declared above still matches
    # first - Starlette checks routes in registration order, and a mount
    # only catches whatever no earlier route claimed. html=True serves
    # static/index.html for "/" and lets the browser 404 naturally on a
    # bad asset path instead of falling through to index.html (SPA-style
    # catch-all routing would be wrong here: there is exactly one page).
    # Changes whenever the process restarts, which in this project means
    # whenever the image was rebuilt - exactly the moment stale modules must
    # be dropped.
    build_id = str(int(time.time()))

    @app.middleware("http")
    async def version_module_imports(request: Request, call_next):
        """Rewrite relative ES-module specifiers to carry ?v=<build_id>.

        Cache-Control alone does not solve this. A browser keys module cache
        by URL, and index.html's `import "./js/main.js"` plus main.js's
        `import "./town.js"` never change URL, so a rebuilt image serves
        corrected code that the tab simply never fetches. Observed for
        several fix cycles: the server returned a world-sized night overlay
        and a 0.38 tint ceiling while the live page kept reporting a
        viewport-sized overlay at 0.72, making working fixes look broken.
        Appending a per-boot version to every relative import makes the URL
        change on rebuild, so the whole module graph is re-fetched.
        """
        response = await call_next(request)
        path = request.url.path
        if not (path.endswith((".js", ".html")) or path == "/"):
            return response
        # UI-19. The generated cast is exempt.
        #
        # `agents.js` is ~1.7 MB and is SERVED FROM A DYNAMIC ROUTE, so
        # every request for it was fully buffered, decoded to str, regex-
        # scanned, re-encoded and re-sent - per request, per browser tab,
        # on the hot path of the page a person actually watches.
        #
        # And for nothing: the rewrite exists to version RELATIVE ES-module
        # specifiers so a rebuilt image is re-fetched, and `agents.js`
        # contains no imports at all. It is one `const AGENTS = [...]`.
        # Its route already sets its own cache headers.
        if path.endswith("/agents.js"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        text = body.decode("utf-8", errors="replace")
        text = MODULE_IMPORT_RE.sub(
            lambda m: f'{m.group(1)}"{m.group(2)}?v={build_id}"', text
        )
        data = text.encode("utf-8")
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["Cache-Control"] = "no-store, must-revalidate"
        return Response(
            content=data,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    @app.middleware("http")
    async def no_cache_frontend(request: Request, call_next):
        """Never let a browser cache the JS/CSS/HTML of the town view.

        Browsers cache ES modules aggressively, and index.html imports
        js/main.js with no version query. During development that means a
        rebuilt image can serve corrected code while the tab keeps running
        the old module - observed repeatedly: the server returned the fixed
        day/night ceiling (0.38) and a world-sized night overlay while the
        live page still reported the previous 0.72 and a viewport-sized one,
        which made several fixes look like they had failed when they had
        simply never loaded. Map assets (the tileset PNGs and Tiled JSON)
        are large and effectively immutable, so they keep normal caching.
        """
        response = await call_next(request)
        path = request.url.path
        if path.endswith((".js", ".css", ".html")) or path == "/":
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    # UI-10: keyed on a fingerprint of WHO the cast is and what they are
    # called, not just on the set of ids. A rename or a newborn reusing an
    # id left the browser holding a stale 1.7 MB document indefinitely,
    # because the id set - the only thing the old key looked at - had not
    # changed.
    def _cast_fingerprint(snapshot) -> str:
        """Who the cast is, and what the viewer draws them as (UI-10).

        Cheap and total: id, display name and whether they are alive, for
        every resident, hashed. A rename, a death or a newborn all move it;
        a tick that changed nobody does not, so the 1.7 MB body is still
        served from cache on the common path.
        """
        import hashlib

        rows = sorted(
            f"{a.agent_id}|{getattr(a, 'display_name', '')}|{getattr(a, 'is_alive', True)}"
            for a in (snapshot.agents or [])
        )
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()

    # UI-10: keyed on a fingerprint of WHO the cast is and what they are
    # called, not just on the set of ids. A rename or a newborn reusing an
    # id left the browser holding a stale 1.7 MB document indefinitely,
    # because the id set - the only thing the old key looked at - had not
    # changed.
    _cast_cache: dict[str, object] = {"key": None, "body": None}

    @app.get("/js/township/agents.js")
    async def agents_js() -> Response:
        """Serve the cast cut down to the agents a bound run actually reports.

        `agents.js` is a 1.7 MB file holding all 314 build-time residents, and
        it is the source the map and every panel read from. Trimming it in the
        browser was the wrong place: the page renders before the trim lands,
        several renders run on their own timers and put the old numbers back,
        and a cached document skips the trimming script altogether. The viewer
        kept reporting "29 of 315" while the world held 30.

        Cutting it here removes the race by removing the data. The browser is
        handed a file containing 30 residents, so there is no moment at which
        anything can show 314 - no ordering to get right, nothing to
        re-render, and a stale cache cannot resurrect a cast it never had.

        With no world bound the file is served whole: the full town is the
        honest answer when there is no run to be narrower than.
        """
        path = STATIC_ROOT / "js" / "township" / "agents.js"
        raw = path.read_text(encoding="utf-8")

        client = live_world_client()
        live_ids = None
        cast_fingerprint = None
        if client is not None:
            try:
                snapshot = await client.get_snapshot()
                # `agent_id`, not `id` - the Snapshot contract names it
                # that way, and guessing wrong here fails silently: the
                # except below swallows it and the whole 314-cast is served,
                # which is exactly the bug this route exists to fix.
                live_ids = {a.agent_id for a in (snapshot.agents or [])}
                # UI-10: the cache key. Ids alone miss a rename and a
                # newborn who reuses an id, so the browser kept a stale
                # document until the id SET changed - which it rarely does.
                cast_fingerprint = _cast_fingerprint(snapshot)
            except Exception as exc:  # noqa: BLE001 - see below
                # UI-10. A blip is not an empty town.
                #
                # This used to fall through to the `not live_ids` branch and
                # serve `const AGENTS = []`, which the browser then CACHES
                # as a document - so one momentary hiccup while the world
                # was restarting left the page permanently showing a town
                # with nobody in it, and a reload was the only cure.
                #
                # 503 + Retry-After is the difference between "there is
                # nobody" and "ask me again in a second". The empty cast
                # below stays, for the case it is actually true.
                logger.warning("world unreachable while serving the cast: %s", exc)
                return Response(
                    status_code=503,
                    content="// the world is not answering; retrying\n",
                    media_type="application/javascript",
                    headers={"Retry-After": "2", "Cache-Control": "no-store"},
                )
        if not live_ids:
            # No run bound: serve an EMPTY cast, not 314 invented residents.
            #
            # The build-time cast is the last mock in the viewer. Shipping it
            # when nothing is running fills the map, the roster and every
            # figure with people who are not in any simulation - and there is
            # no way for a reader to tell them from the real ones. An empty
            # town is the honest picture of "nothing is running", and the
            # panels already say so in words.
            head_only = raw[: raw.find("const AGENTS = ")]
            return Response(
                head_only
                + "// No world is bound, so there are no residents to show.\n"
                + "// The full cast is still on disk; it is simply not served\n"
                + "// unless a run reports who is real. See game/server.py.\n"
                + "const AGENTS = [];\n",
                media_type="application/javascript",
            )

        if _cast_cache["key"] == cast_fingerprint and _cast_cache["body"]:
            return Response(_cast_cache["body"], media_type="application/javascript")

        # A cast the build-time roster has never heard of.
        #
        # agents.js holds the 314 residents of world/genesis.json. A run
        # against any OTHER cast - genesis_10.json's ten neighbours, ids
        # n01..n10 - intersects it in nothing, so the filter below produced
        # an empty array and the viewer drew an empty town while ten engines
        # were demonstrably alive.
        #
        # The world knows who these people are. Building the rows from it is
        # the only honest source: no build step to remember, and every field
        # is the run's own rather than a stranger's borrowed for the shape.
        if not (live_ids & _roster_ids(raw)):
            rows = await _cast_rows_from_world(client)
            if rows:
                head_only = raw[: raw.find("const AGENTS = ")]
                body = (
                    head_only
                    + "// This cast is not in the build-time roster, so it is\n"
                    + "// built from the running world. See game/server.py's\n"
                    + "// agents_js().\n"
                    + "const AGENTS = " + json.dumps(rows, indent=1) + ";\n"
                )
                _cast_cache["key"], _cast_cache["body"] = cast_fingerprint, body
                return Response(body, media_type="application/javascript")

        # Locate the array by bracket-matching, not by searching for "];".
        # rpartition finds the LAST one in the file - well past the array -
        # so the reassembled file was not parseable JavaScript at all:
        # `AGENTS` came out undefined and the viewer went blank.
        marker = "const AGENTS = "
        at = raw.find(marker)
        start = raw.find("[", at) if at >= 0 else -1
        if start < 0:
            return Response(raw, media_type="application/javascript")

        depth, end, in_str, esc = 0, -1, False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return Response(raw, media_type="application/javascript")

        head, tail = raw[:start], raw[end + 1:]
        try:
            everyone = json.loads(raw[start:end + 1])
        except ValueError:
            return Response(raw, media_type="application/javascript")

        names = {a.agent_id: a.display_name for a in (snapshot.agents or [])}
        kept = []
        for a in everyone:
            if a.get("id") not in live_ids:
                continue
            # Adopt the running world's name. agents.js is a different draw of
            # the town and calls a0039 "Felix Ward" where this run says "Ezra
            # Lund"; the prose carries it too, so the sentence is rewritten
            # rather than left contradicting its own heading.
            real = names.get(a["id"])
            if real and real != a.get("n"):
                was = a.get("n") or ""
                a = dict(a)
                a["castName"], a["n"] = was, real
                first_was, first_now = was.split(" ")[0], real.split(" ")[0]
                for field in ("bio", "tension", "fear", "ambition", "values",
                              "limit", "scarcity", "habit"):
                    v = a.get(field)
                    if isinstance(v, str) and v:
                        v = v.replace(was, real)
                        if first_was and first_was != first_now:
                            v = v.replace(first_was, first_now)
                        a[field] = v
            kept.append(a)
        if not kept:
            # None of the run's ids are in this cast: an empty town would be
            # worse than the whole one.
            return Response(raw, media_type="application/javascript")

        out = head + json.dumps(kept, separators=(",", ":")) + tail
        out = out.replace(
            marker,
            f"// Cut to the {len(kept)} residents this run reports "
            f"(game/server.py). The file on disk still holds all "
            f"{len(everyone)}; nothing was deleted.\n" + marker,
            1,
        )
        _cast_cache["key"] = cast_fingerprint
        _cast_cache["body"] = out
        return Response(out, media_type="application/javascript")

    @app.middleware("http")
    async def never_cache_the_page(request: Request, call_next):
        """Stop the browser serving a stale township-v2.html.

        Versioning the assets (`live-cast.js?v=...`) cannot help if the HTML
        that *carries* those references is itself cached: the browser replays
        an old document with old script tags and never learns the new ones
        exist. That is exactly what happened - a viewer kept showing 314
        residents and a climbing death toll from the retired client-side
        model, while the server had been serving the corrected page for hours
        and every fresh browser got it right.

        Only the HTML. The fingerprinted assets are safe to cache forever, and
        making them uncacheable would re-download a 1.6 MB cast on every
        poll.
        """
        response = await call_next(request)
        path = request.url.path
        if path.endswith(".html") or path in ("/", ""):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/", StaticFiles(directory=str(STATIC_ROOT), html=True), name="static")

    return app


app = create_app()
