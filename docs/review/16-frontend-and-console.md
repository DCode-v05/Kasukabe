*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`UI-nn`) are referenced from the cross-cutting documents.*

# Review G: the frontend (viewer, viewers, President/God console, observatory)

Prefix: **UI**. Branch `Real` @ 5400e94, 2026-09-03. Read-only review; every claim cites `path:line` under `/home/user/kasukabe/app/` unless stated. Line numbers for the large JS files were taken from `sed -n` with offsets and are accurate to +-1.

Test runs performed (cheap, allowed by the brief): `cd app && uv run pytest game -q -x` -> **285 passed, 1 warning in 6.25s**. `node --check` over every `.js` in `game/static/js/**`, `game/static/js/township/*.js` and `game/console/static/console.js` -> clean (node v22.22.2). I did not run the top-level `tests/test_console_world_integration.py` (it boots real uvicorn servers) and did not run the world suite.

---

## 0. Executive summary (the nine questions, in one screen)

1. **Inventory.** Five pages plus a console and an observatory. `township-v2.html` is the page everyone uses. It is a hybrid: positions, names, skills, activity, talk, group chats, vitals, workshop, council, authored and preflight are LIVE from the run; the Money book, Government book (seat holders, treasury, land register, initiatives), People cards, map cards, the inspector's Profile/Family/Estate/Memory/Experts/Money tabs, the shop "SOLD OUT/LOW" signs, the PM/minister sashes, the day/clock label, and the alive/dead gate on which real agents get drawn are an **in-browser simulation** (`township-v2.js`, 5,481 lines) that still runs on every frame. `index.html` (Phaser) is live-but-broken against the production host (no `/ws/events`, hashed positions). `agents.html` and `views.html` Scenarios/Conversations read routers that the production host never mounts, so they 404 against a real run. The console (`:8300`) is fully wired to `/admin/*` on both hosts.
2. **Two maps.** The engine walks the Ville Tiled JSON (`world/map`); the township viewer draws a procedurally generated 104x104 town whose districts are a fixed table (`village2.js:440-461`). The bridge is a name->district lookup (`position.py:227-437`). Expansion appends rows/columns to the Tiled JSON on the standalone host only (`admin_api.py:642-651`); the production (embedded) host has `map_service=None` (`run_genesis.py:2176`), so approval records a status and grows nothing. No viewer renders a new tile or building: the Phaser page re-fetches `/api/map`, but `/api/map` serves the static on-disk genesis JSON forever (`world_client.py:484,188-207`); the township map has no API to add a building (`village2.js:2263-2363` exposes `setAgents`/`setLabelBadges` only) and its districts are hard-coded.
3. **DESIGN §8 viewers.** Location viewer: partial (township place card, modelled roles; world `/places/{id}` exists but nothing renders it). Governance + change log: built (`views.html` Governance, real diff) and works against both hosts. Voting: built, real routes. Scenario feed and Conversations (views.html): built but read `social_api`, which only `world/main.py` mounts -> 404 on a live run; the township Talk/Group-chats books are the live substitute. Agent finder/activity/reasoning: built (`agents.html`) but read `agent_viewer_api`, only mounted on `world/main.py` -> 404 on a live run; the reasoning viewer additionally needs `llm_calls` in the world's trace store.
4. **Console.** Every route in `game/console/world_client.py` is served by `world/admin_api.py` and mounted on **both** hosts (`world/main.py:141`, `run_genesis.py:2183`), and `tests/test_console_world_integration.py` proves it end to end. STATUS.md risk 2 is closed as a contract; what is not closed is the effect: on the production host approval of an expansion grows no map; the baby-blessing queue can never be non-empty because no tool or HTTP route submits a birth request (only `wiring.WorldServer.submit_birth_request`, a Python method on the standalone host, `world/wiring.py:1292`); prayers do reach the feed (`world/tools/primitives/social.py:440`). An operator today **can** approve an expansion (status only) and **cannot** approve a birth.
5. **Live mode.** `township-v2.js` polls `/api/township/live` every 2 s (`:1095-1179`, interval `:5475`), `/api/engine` every 2 s, `/api/cost` every 3 s, `/api/town/talk`+`/authored` every 5 s, `/api/town/conversations` every 5 s, `/api/town/vitals` every 6 s, `/api/town/workshop`+`/council` every 15 s, `/api/town/preflight` every 30 s, `live-cast.js` re-polls `/api/township/live` and `/authored` every 10 s -> roughly 2.5 requests/second per open tab, each `/api/township/live` fanning into three upstream calls. "bound n of N" is correct only in the sense that server `bound` counts every agent with any non-empty `location_id` (unknown places are forced to the square, `server.py:71-80`), and the browser N is the cast after the server cuts `agents.js` to the run's ids (`server.py:854-1013`), so n==N whenever the world is up. The WS relay targets `/ws/events`, which the production host does not serve.
6. **Observatory :8500.** `app/observatory/` (FastAPI, read-only: trace SQLite + `/world/refusals` + `/world/engines`). Served only by `docker-compose.yml:147-164` (`WORLD_BASE_URL=http://genesis:8100`, a service that does not exist in that file). `runseries.sh` starts broker and world only; nothing in the run scripts starts it, and its default DB path `data/kasukabe.db` (`observatory/app.py:48`) differs from the series harness's `data/<name>.db` (`runseries.sh:58`). That is why run 23 reported "Observatory: not reachable on :8500" (`docs/runs/run-23-report.md:7324`).
7. **Code quality.** `township-v2.js` 5,481 lines / 272 KB, classic script, no IIFE, ~3,000 lines of it a simulation the page claims to have removed. `township.js` (v1, 2,697 lines) and `village.js` (v1, 1,576 lines) are near-verbatim ancestors still loaded by `township.html`; `archive/village3.js` (1,255) is dead. Docstrings in `server.py`, `mock_world.py`, `contracts.py`, `game/README.md`, `HANDOVER.md` describe defaults and contracts that no longer exist. Tests: 285 in `game/tests` pass; two are tautological/unreachable (below).
8. **Research-grade UI.** Nothing exists for run selection, tick scrubbing, replay from the trace, per-agent goal/belief panels, or time-series economy charts. Building blocks that do exist server-side: `/governance/*` history routes, `/family/money-flow`, `/businesses/pnl` (by_day), `/world/vitals`, `/world/refusals`, `agent_viewer_api` reasoning over `llm_calls`, the trace SQLite schema, `world/run_report.py`.
9. **Other breakage.** Real agents can be hidden from the map by the browser's own illness/starvation model; fabricated newborns (`b0001`...) can be pushed into the live cast; real occupations are silently rewritten at load (`retrade`); two writers fight over the HUD alive/dead counts; `data/characters/` referenced by the inspector is empty; `game/realsim.py` imports `world/` (boundary rule 2); the Real-sim button launches `docker compose run` with a `broker-real` service that no compose file defines.

---

## 1. Inventory

Status legend: **LIVE** = on the path `world.run_genesis` / `game.server` / the pages those serve; **LIVE-STANDALONE** = only reachable when `world/main.py` (docker `world` service) is the host, which no run script uses; **TEST-ONLY**; **ORPHANED** = no production caller.

### 1.1 `app/game/` Python

| Path | Lines | Purpose | Status / proof |
|---|---|---|---|
| `game/__init__.py` | 36 | package marker | LIVE |
| `game/config.py` | 227 | `GameSettings` (env), `mock_mode` default **False** (`:90`), `MOCK_MODE` env rejected (`:167-171`) | LIVE: `server.py:212` |
| `game/server.py` | 1044 | FastAPI: `/api/snapshot`, `/api/township/live`, `/api/town/*`, `/api/realsim/*`, `/api/engine`, `/api/cost`, `/api/rules`, `/api/map`, `/api/places`, `/api/spend`, `/api/agents/{id}`, `/ws`, `/js/township/agents.js` (dynamic), static mount at `/` | LIVE: `Makefile:16`, `docker-compose.yml:184` |
| `game/relay.py` | 102 | `ConnectionHub` fan-out of world WS events to browser tabs | LIVE: `server.py:214` |
| `game/world_client.py` | 725 | `HttpWorldClient` (real), `Place`, `GENESIS_PLACES`, `SpendStatus`, `WorldClient` Protocol | LIVE: `server.py:100` |
| `game/mock_world.py` | 474 | `MockWorldClient`, scripted 12-agent town | TEST-ONLY: only reachable with `GameSettings(mock_mode=True)` which env cannot set (`config.py:164-171`, `server.py:98-99`); constructed by `game/tests/*` |
| `game/contracts.py` | 214 | pydantic wire shapes (`Snapshot`, `AgentSummary`, `AgentDetail`, `WireEvent`) | LIVE (used by server, world_client, relay). Its CONTRACT docstring (`:27-52`, `/api/game/snapshot`) is fiction, see UI-30 |
| `game/position.py` | 437 | day/night tint, sprite roster, hashed fallback tile, **`township_zone_for` / `township_zone_detail`** (name -> district bridge) | LIVE: `server.py:55,77`, `world_client.py:81` |
| `game/realsim.py` | 432 | "Real sim" button: writes a genesis subset, `docker compose run` the runner, survivor watcher | LIVE: `server.py:59,217`. Imports `world.make_genesis_subset` (`:237`) -> boundary violation UI-14 |
| `game/console/app.py` | 540 | console FastAPI, `/api/president|god|spend|alerts|control|town/*` | LIVE-CONSOLE: `Makefile:19` |
| `game/console/world_client.py` | 328 | `WorldAdminClient` -> `{world}/admin/*` + read-only town routes | LIVE-CONSOLE |
| `game/console/schemas.py` | 435 | console's copy of the admin wire shapes | LIVE-CONSOLE |
| `game/console/framing.py` | 281 | confidence-labelled sentences for the Town tab | LIVE-CONSOLE |
| `game/console/auth.py` | 70 | bearer/`X-Operator-Token` check | LIVE-CONSOLE |
| `game/console/config.py` | 164 | `ConsoleSettings` | LIVE-CONSOLE |
| `game/console/errors.py` | 50 | error hierarchy | LIVE-CONSOLE |
| `game/console/static/{index.html,console.js,console.css}` | 205/683/249 | the console page | LIVE-CONSOLE |
| `game/tests/*.py` (10 files) | 2,254 | unit tests | TEST-ONLY, all pass |
| `game/console/tests/*.py` (10 files) | ~1,560 | console tests incl. `fake_world_admin.py` | TEST-ONLY |
| `app/tests/test_console_world_integration.py` | 370 | console <-> real world admin API on both hosts | TEST-ONLY (real integration) |
| `app/observatory/{app,health,hops,gaps}.py` + `static/index.html` | 197/82/60/72/200 | read-only instrument on :8500 | ORPHANED in practice: only `docker-compose.yml:147-164` launches it; no run script does (UI-11) |

### 1.2 Pages and JS

| Page | Scripts | Reads | Status |
|---|---|---|---|
| `static/township-v2.html` (335) | `rules.js`(767, generated), `agents.js`(6 lines/1.7 MB, generated, served cut by `server.py:854`), `village2.js`(2,385), `part-e.js`(354), `township-v2.js`(5,481), `shell.js`(319), `live-cast.js`(248), `vitals.js`(185), `talk.js`(365), `groupchat.js`(129), `preflight.js`(123) | `/api/township/live`, `/api/engine`, `/api/cost`, `/api/rules`, `/api/town/{talk,authored,conversations,vitals,workshop,council,preflight}`, `/api/realsim/*` | LIVE, hybrid (see §2.3) |
| `static/township.html` (196) | `agents.js`, `village.js`(1,576), `township.js`(2,697) | none (pure in-browser sim; no live block) | ORPHANED: not linked from any page; kept "for comparison" (HANDOVER §1) |
| `static/index.html` (50) | `main.js`(119), `town.js`(729), `inspector.js`(130), `daynight.js`(53), `ws_client.js`(47), `api.js`(22), `bus.js`(30), Phaser 3.55 CDN | `/api/snapshot`, `/api/map`, `/api/agents/{id}`, `WS /ws` | LIVE but non-functional against the production host (UI-6) |
| `static/agents.html` (98) | `js/agentviews/{main,state,api,dom,finder,activity,reasoning}.js` (89/21/74/59/128/102/178) | cross-origin `{world}/agent-viewer/agents[...]` | LIVE-STANDALONE only (router not mounted on embedded host, UI-2) |
| `static/views.html` (128) | `js/views/{main,api,dom,governance,voting,scenarios,conversations}.js` (68/121/108/200/103/107/93) | cross-origin `{world}/governance/*`, `/voting/*`, `/scenarios*`, `/conversations*` | Governance+Voting LIVE on both hosts; Scenarios+Conversations LIVE-STANDALONE only (UI-2) |
| `static/js/township/archive/village3.js` (1,255) | - | - | ORPHANED (archive README says so) |
| `static/js/township/build_{agents,characters,rules}.py` (216/935/77) | generators | - | build-time only; `build_agents.py` is superseded by `build_characters.py` (both write `agents.js`), UI-27 |
| CSS: `css/township.css`(1,936), `instrument.css`(368), `parts/{controls,books,resident,overlays,revamp}.css`(331/343/376/362/940), `style.css`(288), `agentviews.css`(190), `views.css`(271) | 5,405 lines | - | LIVE; township.css shared by v1 and v2 pages |

---

## 2. How it works today

### 2.1 Process topology

- **Production run** (`runseries.sh`, `run50.sh`, `start-genesis.sh`, the Real-sim button): broker on :8200, then `python -m world.run_genesis --world-mode embedded` which boots `build_embedded_world_app` (`world/run_genesis.py:2102-2226`) on :8100. That app mounts: `/health`, `/tools/registry[/version]`, `/agents/{id}/tool_calls`, `/agents/{id}/memories`, `/ledger/*`, `/metrics`, `/world/{preflight,vitals,utterances,conversations,activity,authored,refusals,engines}` (`:2258-2917`), the `/admin/*` router when a tick engine is supplied (`:2147-2183`, `map_service=None` at `:2176`), and when a genesis is supplied the agents, location, governance, family, skills and workshop routers (`:2188-2226`), all with `map_service=None`. **Not mounted:** `social_api` (`/scenarios`, `/conversations`), `agent_viewer_api` (`/agent-viewer/*`), `/ws/events`, `/state/summary`, `/map/tiled` works but 503s (`agents_api.py:495-507`).
- **Standalone world** (`world/main.py`, `make run-world`, docker `world`): mounts everything above plus social, agent-viewer, `/ws/events` (`main.py:141-205, 626`), with a real `MapService`. No run script uses it; it runs no engines (`cognition: "none"`, `main.py:210-220`).
- **Game server** (`game/server.py`, :8400) proxies to `WORLD_BASE_URL` (default `http://localhost:8100`). `live_world_client()` (`server.py:219-227`) prefers the Real-sim run's client for the township routes, but `/api/snapshot`, `/api/map`, `/api/places`, `/api/spend`, `/api/agents/{id}` and the WS relay keep the process-start client (`server.py:249-255, 708-766`) - so the Phaser page and the township page can be looking at different worlds (UI-20).
- **Console** (`game/console/app.py`, :8300) -> `{world}/admin/*` with the operator token; `/api/town/*` proxies the read-only `family/`, `businesses/pnl`, `governance/`, `skills/` routes.
- **Observatory** (:8500) reads the broker's SQLite directly and `{world}/world/refusals`, `/world/engines`.

### 2.2 The township live path, request by request

1. Browser loads `township-v2.html`; `<script src="js/township/agents.js">` hits the dynamic route `server.py:854-1013`: it calls `live_world_client().get_snapshot()`; with no world -> serves `const AGENTS = []` (`:888-905`); with a world whose ids intersect the 314-resident build artefact -> serves the artefact cut to those ids with names rewritten to the world's (`:935-1013`); with a cast the artefact has never seen (e.g. `genesis_20.json`'s `n01..n20`) -> builds rows from the world (`_cast_rows_from_world`, `:119-198`), leaving persona fields blank.
2. `township-v2.js` top level runs immediately: `rebalanceTeachers/Instructors`, `staffThePosts`, `assignWorksites`, `initAgentDerived` (`:421-425`), `assignHomes`, `assignShifts` (`:5427-5428`), `V.mount` (`:5430`), then the render loop `tick()` (`:5251-5365`) via rAF, and `pollLive()` every 2 s (`:5474-5475`).
3. `pollLive` (`:1095-1179`) GETs `/api/township/live`; server (`:257-384`) calls `get_provenance()` (`/health`), `get_snapshot()` (`/world/state` then `/agents`), `get_activity()` (`/world/activity`), and maps each `location_id` through `_drawable_zone` (`:71-80`, `township_zone_detail` in `position.py:380-401`, falling back to `townsquare`). Rows carry `zone`, `zone_exact`, `location_id`, `action`, `doing`, `recent`, `skills`, `following`, `name`, `age_years`, `occupation`, `sex`.
4. The browser keeps rows whose `zone` is truthy **and** whose id is in `AGENT_BY_ID` (`:1124`); `LIVE.connected = bound > 0` (`:1164`); dispatches `kasukabe:live` for `part-e.js`.
5. Each frame, `agentPosition(a, hourFrac)` (`:1928-2114`) returns the live position for a bound resident (`livePosition`, `:1030-1083`: first sighting = stand at a seeded slot in the district keyed by the world's `location_id`; a change starts a `walkRoute` over the street BFS field, `:894-947`, timed at 0.62 tiles/s wall clock scaled by the browser clock speed) and the scripted schedule for everyone else. Before that check it returns `dead -> housing` if the **browser's** `healthSim` says the agent is dead (`:1935`).
6. `V.setAgents(AGENTS.filter(a => isAlive(a) && bound && bound[a.id]) ...)` (`:5284-5334`) hands only bound, browser-alive residents to the renderer; `atWork` for bound residents comes from `isWorkingNow` (WORK_VERBS over the world's phrase, `:4163-4167`).
7. `renderLiveBadge` (`:1296-1367`): `runMode` is the constant `'real'` (`:1200`), so the badge shows `renderEngineBadgeText()` (`:1447-1456`, requires `ENGINE.ok && cognition === 'engine' && bound > 0`, `:1419-1422`) or `REAL SIM · NOT CONNECTED`. The whole `SIMULATED SCHEDULE` / `LIVE (MOCK COGNITION)` branch (`:1328-1367`) is unreachable.
8. `live-cast.js` (10 s) prunes `AGENTS` to the live ids, adopts world names from `/api/town/authored.names`, rewrites HUD alive/dead from `AGENTS.length` (`live-cast.js:86-93`) while `vitals.js` (6 s) writes the world's figures into the same elements (`vitals.js:139-143`).

### 2.3 What is simulated in JS versus read from the world (question 1)

Read from the world when a run is bound: position/district (`LIVE.zone`), current line and recent tool calls (`LIVE.doing`, `LIVE.recent`), name/age/occupation/sex (`LIVE.identity`, `:4114-4131`), skills and following (`part-e.js`), talk/group chats (`talk.js`, `groupchat.js`), vitals (health counts, buildings, food holdings, treasury, town wealth: `vitals.js`), workshop/council (`part-e.js`), preflight, cost/engine readouts, the rulebook (`/api/rules`, `:2246-2256`).

Still computed by the browser on every frame **and shown**, regardless of binding:

| Fiction | Where | Shown in |
|---|---|---|
| Occupation rewriting at load: surplus teachers/instructors re-traded; day labourers/sweepers/artisans conscripted as police | `:263-338, 421-423` | every card/roster/place role of a real agent |
| Wages, upkeep, head tax, drift, `balanceNow`, `costSpent` (exponential decay of the $2 wallet) | `:27-37, 2329-2337, 2506-2523` | Money book (`renderScoreboard` `:3422-3550`, every 1.2 s), map card (`:4610-4612`), People cards (`refreshRosterLiveValues` `:4812-4838`), inspector Money tab (`:4415-4453`) |
| Illness, 85+ hazard, terminal deaths, recoveries (`healthSim`) | `:480-672` | inspector status line (`:4257-4272`), map card (`:4591-4602`), roster status `✝ deceased` (`:4828-4831`), **the draw gate** (`:5284`), `agentPosition` dead branch (`:1935`) |
| Food economy: production by dayZone workers, spoilage, price elasticity, hunger -> illness -> **starvation death** written into `healthSim` | `:2266-2500` (deaths `:2457-2464`) | shop `SOLD OUT/LOW` badges on the map (`refreshShopSigns` `:3555-3575`, 1 s), place-card shelves (`shelfHtml` `:3992-4014`), `isHungry` in activity text |
| Births: married women 18-45 roll 0.4%/day; newborn `b0001..` pushed into `AGENTS` with `usd: 200` | `:2722-2807`, `AGENTS.push` `:2787` | People cards (`addNewbornCards` `:4767-4776`), census, `POP` |
| Government: 5 seat holders picked by hash from clerks, PM/minister sashes and map pins, seat salaries, pensions, building upkeep, treasury walk, 7 initiatives "built · day N", land register, wills, coming of age | `:2847-3040, 2928-2937, 2633-2720, 2536-2626` | Government book (`renderGovernment` `:3268-3381`, 0.4 s), sashes on the map (`:5306-5309`), search ("prime minister" `:3720`), place-card roles (`:3880-3885`) |
| Conversations from templates | `:3117-3255` | now nowhere (`#chatLog` removed) but computed every half game-hour |
| Homes, shifts, guard posts, shopping trips, strolls, guardians | `:437-473, 687-722, 1799-1927` | activity text for unbound agents, place cards, People cards ("works at … · night shift") |
| Skill unlock schedule (`skillUnlockDay`) | `:384-387` | map card (`:4616-4619`), People cards (`:4753-4755`), inspector fallback when unbound |
| The clock: `state.hourFrac` advances at `SEC_PER_HOUR_AT_1X = 60` wall seconds per game hour (`:5223`), independent of the world tick | `:5204-5208, 5251-5256` | "Day N 06:00:00" and phase pill in the controls pane (`township-v2.html:66-70`) |

The header comment at `township-v2.html:10-13` says "Every panel here reads the RUNNING world. The client-side model … has been removed along with those panels." Only the panels were removed (`healthBody`, `buildBody`, `treasuryBody`, `foodBody`, `chatLog` no longer exist so `renderHealth` `:5047`, `renderBuildings` `:4862`, `renderTreasury` `:2996`, `renderFood` `:3578`, `renderChat` `:3228` return early); the model still runs and still feeds everything in the table above.

### 2.4 The two maps and the bridge (question 2)

- Engine: `world/map/service.py` holds `tiled` (Ville JSON + genesis strip), `blocks`, `world`; movement is tile-by-tile over its collision grid. `expand_land` (`service.py:181-213`) deep-copies, `append_rows/columns`, carves a sector, regenerates collision, publishes `world.land.expanded` with the `LandExpansion` summary (edge, depth, new width/height, new sector ids/names) - not the tiles. `stamp_building` (`:218-291`) paints sector/arena layers and emits `world.building.mapped`. `construction.py:138,183` emits `world.building.construction_started/completed` with no coordinates.
- Township: `village2.js` lays a fixed grid (`:62-75`: 6 road bands, `G = CORE1 + 36`), fills terrain rectangles (`:102-115`), generates `buildings` and `props`, then declares `ZONES` from `blockRect`/outlying rects (`:440-461`) plus any building tagged `zone` (`:463-473`). `window.plans = buildings` (`:2378-2384`). The public surface (`:2263-2363`) has no `addBuilding`, `addZone`, or terrain mutation; `bIdx` (`:668-673`) and the walk network (`township-v2.js:830-923`, built once, cached) are computed at load.
- Bridge: `position.py:227-261` exact slugs, `:265-271` prefixes (`home:`, `farmland`, `ore`, `business:`), `:415-432` Ville sector names, `:437` residential markers, `:334-357` trade-word heuristics; `township_zone_detail` returns `(zone, exact)`; `server.py:71-80` forces unknowns to `townsquare`. `test_township_zone_bridge.py:27-42` pins the sector names against `world/map/genesis.py`.
- What breaks on growth: (a) a new sector named by the President (`new_sector_name`) is an arbitrary string -> `township_zone_for` returns None unless a trade word matches -> the resident is drawn at the town square with `zone_exact=false`; (b) a new business at a `business:<id>` location binds to `marketrow` by prefix (`:270`), whatever it is; (c) `/api/map` (`server.py:708-726`) returns `HttpWorldClient._genesis_map_json()` which is the **on-disk** `the_ville_jan7.json` (`world_client.py:188-207, 484`) even though `/map/tiled` now exists on the standalone host - so `town.js:rebuildMapInPlace` (`:323-374`) re-fetches the same unchanged file; (d) `addBuildingMarker` (`town.js:382-391`) returns without drawing because the real events carry no `tile_x`; (e) the township page listens to none of these events at all (it has no WS client; grep for `land.expanded`/`building` in `js/township/*` returns nothing).

### 2.5 The console (question 4)

`console.js` renders four tabs (`index.html:57-62`): President (petitions with edge/depth/sector inputs `:130-179`, amendments sign/veto `:183-214`, broadcast, honours), God (prayers grant/silent-deny `:280-310`, blessings `:314-346`, disasters `:350-381`, miracle/misfortune `:382-409`, dials `:413-440`), Town (framed sentences `:571-655`), Alerts (`:510-521`); a control strip (pause/resume/pacing/checkpoint `:539-563`) and a spend bar with a **hard-coded** `RUN_BUDGET_CAP_USD_CENTS = 40000` (`:463`). All refresh every 8 s (`:10`, `:680`).

World side, `world/admin_api.py` serves all of it: petitions `:624-681`, amendments `:683-711`, broadcast/honours `:713-790` (event-log projections), prayers `:792-845`, blessings `:847-914`, disasters `:916-1010`, miracles/misfortunes `:1014-1088`, dials `:1090-1108`, spend `:1111`, alerts `:1130-1196` (regime change, amendment tabled, >= threshold `crime.case.opened`, org membership fraction), control `:1200-1240`. Mounted on both hosts. The integration test proves petitions, prayers (silent deny leaves no agent-visible payload), pause really freezes ticks, spend pools sum to $1000, and a wrong token is 401 (`tests/test_console_world_integration.py:107-322`); map growth is proven only on the standalone host (`:324-370`).

What feeds the queues on the production host: petitions via the `petition` tool (`world/tools/primitives/civic.py:117` -> `charter.land_petitions.file_petition`); prayers via `pray` (`social.py:440`); amendments via the charter engine; births via nothing (§0.4). Broadcast/honours are written but not delivered anywhere an agent perceives (they are events; whether perception surfaces them is out of my scope - flagged as open question).

### 2.6 The other pages

- `index.html`/`town.js`: Phaser tilemap from `/api/map`, sprites at `tile_x/tile_y` from the snapshot; `spawnAgent` skips agents without coordinates (`town.js:460-469`); tile positions come from `position.fallback_tile_for` (sha256 hash of the location string, `position.py:176-190`) because the world's `/agents` row has `tile_x=None` on the embedded host (`agents_api.py:108-109` with `map_service=None`). Moves arrive only via WS `agent.moved` (`town.js:662-668`).
- `agents.html`: Finder/Activity/Reasoning over `/agent-viewer/*` (`agentviews/api.js:57-73`), cross-origin to `:8100` by default (`:10-17`).
- `views.html`: Governance (`/governance/charter`, `/active-policies`, `/government`, `/amendments` with unified diff, `/complaints`), Voting (`/voting/elections[/id]`), Scenarios (`/scenarios`, `/scenarios/{id}/trace`), Conversations (`/conversations`) (`views/api.js:69-121`).

---

## 3. Findings

Severity: P0 blocks the target / corrupts data or money; P1 major gap; P2 defect; P3 polish. Layer: frontend / world / seam / registry / broker / engine-finding.

### UI-01 (P1, world+frontend) The production host cannot grow the map; the two viewers cannot show growth even when it does
- `run_genesis.py:2176` `map_service=None` in the embedded app's `AdminDeps`; `admin_api.py:642-663` records the approval and logs "no physical map growth applied". `agents_api.py:499-501` `/map/tiled` -> 503; `location_api` gets `map_service=None` (`run_genesis.py:2203`).
- Even on the standalone host: `game/world_client.py:484` returns `self._genesis_map_json()` (`:188-207`, the on-disk `the_ville_jan7.json`) - it never calls `/map/tiled`; `town.js:371` re-fetches `/api/map` after `world.land.expanded` and gets the same bytes. `township-v2.js` has no listener for `world.land.expanded`, `world.building.*`, no WS client, and `village2.js` has no way to add a district or building at runtime.
- Why it matters: the target's "town expands on its own… construction appears on screen" has no display path and, on the host every run uses, no physical map either. DESIGN §1 "the town physically grows" is currently true only in `tests/test_console_world_integration.py:329-370`.
- Fix: (world, M) construct a `MapService` in `GenesisRunner.boot` and pass it into `build_embedded_world_app`'s `AdminDeps`/`AgentsDeps`/`LocationDeps` (the standalone host shows the shape at `main.py:117-126, 154`); (frontend, S) make `HttpWorldClient.get_snapshot`/`get_map` call `/map/tiled` and fall back to disk only on 404; (frontend, L) see UI-33 for a single map. Effort M+S+L.

### UI-02 (P1, world) The DESIGN §8 research viewers are unreachable on a live run
- `run_genesis.py:2188-2226` mounts agents, location, governance, family, skills, workshop routers only. `social_api` (`/scenarios`, `/conversations`) and `agent_viewer_api` (`/agent-viewer/agents…/reasoning`) are mounted only at `main.py:199-200`. `grep -n "social_api\|agent_viewer" world/run_genesis.py` -> nothing.
- `agents.html` (all three tabs) and `views.html` Scenarios/Conversations therefore 404 against every run this project has done. The reasoning viewer also needs `AgentViewerDeps.trace` (`main.py:87-94`) pointed at a store holding `llm_calls`; the broker writes those into `TRACE_SQLITE_PATH`, the embedded world has no `trace_store` attribute to hand over.
- Fix (world, S-M): import and mount both routers in `build_embedded_world_app` with `SocialDeps(world, genesis, trace=<runner's trace client>)` and `AgentViewerDeps(world, genesis, tick_engine, trace=...)`; the runner already opens the trace DB for the recorder (SYSTEM_REVIEW §1.6). Acceptance: `GET :8100/agent-viewer/agents` and `GET :8100/scenarios` return 200 from a `runseries.sh` world.

### UI-03 (P1, frontend) The browser's own health/food model can hide a real, living agent from the map
- `township-v2.js:5284` `V.setAgents(AGENTS.filter(a => isAlive(a) && bound && bound[a.id]) …)`; `isAlive` reads `healthSim` (`:671-672`), advanced every frame to the **browser** day (`:5265`, `advanceHealthTo(day-1)`), where the browser day is `state.hourFrac/24` at 60 s per game hour (`:5223`) - unrelated to the world tick. `stepHealthDay` rolls terminal (0.08 %/day), 85+ (0.4 %/day), untreated chronic hazards (`:599-656`); `stepFoodDay` kills after `starvation_days_to_death` hungry days (`:2457-2464`) using production from the cast's `dayZone` workers - a 20-agent cast has few farmers, so shortage is the normal state.
- At 10x, 24 browser-minutes is a browser-day; a multi-hour run at 10x reaches day 50+; a resident the world reports alive is then drawn nowhere, `agentPosition` returns them "buried at home" (`:1935`), the roster says `✝ deceased` (`:4828-4831`) and the inspector says "died day N · starvation". `live-cast.js` never resets `healthSim` for pruned casts either.
- Fix (frontend, S): when `LIVE.connected`, never consult `healthSim`/`foodSim`: gate the draw on `row.alive` from the poll (already carried, `server.py:348`), and in `agentPosition` check `livePosition` before the dead branch. Longer term delete the model (UI-04). Acceptance: a Playwright/headless test that binds a fake `/api/township/live` with 5 alive agents, advances `state.hourFrac` by 60*24 h, and asserts 5 sprites are still handed to `V.setAgents`.

### UI-04 (P1, frontend) A ~3,000-line in-browser simulation still paints fiction over a real run
- Everything in §2.3's table. Concrete contradictions: Government book names five hash-picked clerks as PM/ministers with sashes on the map (`:2928-2937, 5306-5309`) while `/governance/government` knows the real seats; Money book ranks wealth from modelled wages while the world's `sim_balance` is on the same poll (`AgentRow.sim_balance`, `agents_api.py:120`) and unused; People cards show "wage +125 · upkeep −55 · tax −30 / day" and "🔒 unlocks day 3" for engine agents; shop signs say `SOLD OUT` from `foodSim` while `vitals.js` shows the world's real holdings two panels away; `stepBirths` (`:2793-2802`) pushes `b0001 …` into `AGENTS` and `addNewbornCards` (`:4767`) gives them People cards; `retrade`/`staffThePosts` (`:255-338`) rewrite `a.occ` of real agents (a genesis `day_laborer` becomes a `police_officer` on the card, the place-card roles, and the map costume).
- `township-v2.html:10-13` claims the model is gone; `HANDOVER.md` §7.3.8 admits the split. `activityFor` (`:2129-2135`) and `dayPlaceLineFor` (`:4933-4936`) already show the pattern that fixes it (world wins when bound).
- Fix (frontend, L): split `township-v2.js` into (a) `live/` modules that render only world data (positions, identity, activity, money from `sim_balance`/`usd_wallet_cents`, seats from `/governance/government`, buildings from `/world/vitals`), and (b) an optional `demo/` bundle for the file:// showcase; delete `healthSim`, `foodSim`, `stepBirths`, `seatHolders`, `WAGE`, `INITIATIVES`, `retrade`, `staffThePosts`, `LINES`. Acceptance: with a bound world, grep the rendered DOM for any of `unlocks day`, `Prime Minister` (unless `/governance/government` has one), `starvation`, `b0001` -> none.

### UI-05 (P1, world) The baby-blessing queue can never fill on a live run
- `admin_api.py:851-855` lists `deps.extras.birth_requests`; the embedded host creates `AdminExtras(operator_token=…, sim_control=…)` (`run_genesis.py:4220`) and nothing ever writes to `.birth_requests` (grep: only `wiring.py:1292-1340` on the standalone `WorldServer`, a Python method with no HTTP route and no tool). No tool under `world/tools/` mentions `birth_request`.
- DESIGN §5/§8 "baby blessing queue… 5,000 sim to god" and the target's population growth have no entry point. The console's Grant button works (`admin_api.py:857-892` does the real birth) on a queue that is always empty.
- Fix (registry, M): a `request_child` tool (co-located married couple at the temple, fee 5,000 sim per DESIGN §3) that calls `life_birth.submit_birth_request` and stores into the host's `AdminExtras.birth_requests` (share the dict with the embedded app the way `prayer_requests` lives on `WorldState`; simplest is to move `birth_requests` onto `WorldState` so both hosts and checkpoints see it). Acceptance: integration test files a request via the tool, `GET /admin/god/blessings` lists it, grant creates the newborn and `/agents` grows by one.

### UI-06 (P1, frontend+world) The Phaser page (`index.html`) is dead against the production host
- Relay connects to `ws://…:8100/ws/events` (`config.py:95`); the embedded app has no websocket route (route list: only `main.py:626`). `HttpWorldClient.stream_events` (`world_client.py:658-721`) therefore emits `world.connection.lost` every 2 s forever; no `agent.moved`/`world.tick.advanced` ever arrives, so sprites never move and the clock never advances.
- Positions: `AgentRow.tile_x/tile_y` are None without a map service (`agents_api.py:108-109`), so `fallback_tile_for` hashes the location string into a pseudo-random tile (`position.py:176-190`): the whole town drawn on random tiles.
- Fix (world, S): add `/ws/events` to the embedded app (copy `main.py:626-` `EventBus.subscribe` loop); with UI-01's MapService the rows carry real tiles. Or (frontend, S) retire `index.html` and say so in HANDOVER. Acceptance: `websocat ws://127.0.0.1:8100/ws/events` on a series run streams `world.tick.advanced`.

### UI-07 (P2, frontend) Badge/HUD inconsistencies and a dead badge branch
- `runMode` is the constant `'real'` (`:1200`) so `renderLiveBadge`'s `SIMULATED SCHEDULE` / `LIVE (MOCK COGNITION)` / `REAL ENGINES · MOCK MINDS` branch (`:1328-1367`) is unreachable; the HTML default text `SIMULATED SCHEDULE` (`township-v2.html:76`) shows until the first poll. Against the standalone host (`cognition: "none"`) the map draws bound residents (drawing keys on `LIVE.zone`) while the badge says `REAL SIM · NOT CONNECTED` (`engineIsLive` requires `cognition==='engine'`, `:1419-1422`).
- HUD alive/dead: written by `vitals.js:139-143` (world) every 6 s and by `live-cast.js:86-93` every 10 s from `AGENTS.length` (the `a.dead` field it tests is never set), so the two numbers alternate.
- `bound` on the server counts any agent with a non-empty `location_id` (`server.py:71-80, 378`); the browser's `bound` additionally requires the id in `AGENT_BY_ID` (`:1124`). HANDOVER §4.3's "LIVE ENGINE · n of 314 bound" text no longer exists.
- Fix (frontend, S): delete the dead branch; drive the badge off `LIVE` (bound rows) and show cognition/inference as a second line; remove the HUD writes from `live-cast.js`.

### UI-08 (P2, frontend) `/api/township/live` is heavy and the page polls it from two places
- Each poll = `/health` + `/world/state` + `/agents` + `/world/activity` (`server.py:296-326`), the `/agents` row for 20-50 agents carrying skills and following documents. `township-v2.js` polls every 2 s (`:5475`), `live-cast.js` polls the same URL every 10 s (`live-cast.js:210, 238`), `/api/engine` (which repeats `/health`+`/world/state`+`/agents` and two broker calls) every 2 s (`:1293`). Per open tab ~2.5 req/s to the game server, ~8 upstream req/s to a world process that is also serving every engine's tool calls (10 turns/run were already discarded at the 120 s dispatch limit, brief).
- Fix (frontend, S): one poller (`pollLive`) publishing `kasukabe:live` to all consumers (live-cast, part-e already listen); merge `/api/engine` into the same server handler; 2 s -> world tick cadence (a tick is seconds, not 2 s). Or (world, M) serve one `/world/viewer-snapshot` and push it over `/ws/events`.

### UI-09 (P2, frontend) HTML injection from agent-authored text in the township page
- `activityFor` returns `LIVE.doing[a.id].line` (`:2131-2132`), which the world builds from tool parameters (`run_genesis.py:2503 _phrase_activity`), i.e. model output. It is inserted unescaped at `:4033` (place card), `:4269` (inspector "now" box), `:4606` (map card), `:5137-5143` (What-the-town-is-doing tally keys) and `refreshMapCard` `:4681`. `doingBlock` (`:4194-4230`) escapes the same data, so the authors knew. `liveName` results are inserted raw at `:4470`. `adoptWorldNames` rewrites `a.bio` etc. with world names (`live-cast.js:173-205`) then `buildCard` (`:4721`) inserts `a.bio` raw.
- Fix (frontend, S): route every world/agent string through `esc()` (`:4139-4143`); add `'` to that escaper.

### UI-10 (P2, frontend) `agents.js` cut/build logic has stale-cache and empty-cast edge cases
- `_cast_cache` (`server.py:852, 907-908, 1011-1012`) is keyed on the id set only; a rename or a newborn (same ids) is never re-served until ids change. When the world briefly fails (`:886-887`) the route serves `AGENTS = []`, and a cached browser document then boots the whole page on an empty cast: `POP = 0` (`:22`), `Math.max(...[])` = `-Infinity` in `renderCensus` (`:5162-5170`) -> `NaN%` bar widths, `fiscal()` divides by `POP`.
- Fix (frontend, S): key the cache on `(ids, tick)` or a hash of the rows; serve a `503` with `Retry-After` instead of an empty cast when the world is momentarily unreachable.

### UI-11 (P2, ops) Observatory is not part of any run
- `docker-compose.yml:147-164` is the only launcher; it sets `WORLD_BASE_URL=http://genesis:8100` but no `genesis` service exists in that file (services: test, world, broker, observatory, game). `runseries.sh`/`run50.sh`/`start-genesis.sh` never start it. Default `TRACE_SQLITE_PATH` is `data/kasukabe.db` (`observatory/app.py:48`) while the series harness writes `data/<name>.db` (`runseries.sh:58`). Hence run 23's "not reachable on :8500".
- Fix (ops, S): start `uvicorn observatory.app:app --port 8500` from `runseries.sh` with `TRACE_SQLITE_PATH=$DB WORLD_BASE_URL=http://127.0.0.1:8100`; fix the compose `WORLD_BASE_URL` to `http://world:8100` or add the genesis service.

### UI-12 (P2, world) `/agents/{id}` detail is thrown away by the game client
- `world/agents_api.py:428-444` serves `bio`, `personality_traits`, `habits`, `spouse_id`, `parent_ids`, `children_ids`, `marital_status`, `skills`, `co_located`; `game/world_client.py:286-309` `_agent_detail_from_world` hard-codes `bio=""`, `occupation=None`, `recent_utterances=[]`, `relationships=[]` (its docstring at `:214-226` still says the world serves no name). `_cast_rows_from_world` (`server.py:146-153`) works around it by reaching into `client._get` and re-fetching every agent one by one.
- Fix (frontend, S): map the real fields; drop the private-attribute reach-in.

### UI-13 (P2, frontend) `MockWorldClient` no longer satisfies `WorldClient`
- The Protocol (`world_client.py:313-360`) requires `get_workshop/council/talk/conversations/authored/vitals/preflight/activity`; `MockWorldClient` (`mock_world.py:370-474`) implements none, and `server.py` papers over it with `getattr` (`:296, 322, 397`). Since `mock_mode` cannot be set from the environment it is test-only, but `test_game_server.py:21-26` still exercises the product through it, so those tests exercise the `getattr`-missing branches, not the real ones.
- Fix (frontend, S): either delete `mock_world.py` and rewrite the six tests around `FakeWorld` doubles (as `test_engine_status.py` does), or implement the Protocol.

### UI-14 (P2, boundary) `game/realsim.py` imports `world/`
- `realsim.py:237` `from world.make_genesis_subset import build_subset` and writes `world/genesis_realsim_N.json` into the world package directory (`:241`). HANDOVER §3 rule 2 says `game/` never imports `world/`; `tests/test_module_boundaries.py` only checks boto3 (`:62-74`) so nothing catches it. `test_realsim.py:63-87` imports `world.*` from `game/tests` too.
- Fix (frontend, S): call a world HTTP/CLI entry point (`python -m world.make_genesis_subset`) or move the button into the console; extend `test_module_boundaries.py` with a `game -> world` import check.

### UI-15 (P2, frontend) Real-sim button depends on infrastructure that is not in the repo
- `realsim.py:279-300` runs `docker compose run -d --name kasukabe-realsim … world python -m world.run_genesis --live …` with `--broker-base-url http://broker-real:8200` (`:66, 294`); no compose file defines `broker-real`; the `world` compose service runs from `app/` with `../engine` mounted read-only. `stop()` on a non-container run uses `os.killpg` (`:360`). None of this is exercised by tests (`test_realsim.py` stubs `_terminate`).
- Also a P3 inside: `test_realsim.py:94-98` asserts `controller.run.alive()` - `RunHandle` has no `alive()` method (`realsim.py:79-125`: `alive` is an int field, the method is `alive_process()`); the assertion passes only because `run is None`.
- Fix (frontend, S): add the `broker-real` service or default to `BROKER_BASE_URL`; fix the test.

### UI-16 (P2, frontend) The console's spend cap is hard-coded to $400
- `console.js:463` `RUN_BUDGET_CAP_USD_CENTS = 40000`; runs use $1-$10 caps (brief). The bar and the "of $400 cap" note (`index.html:33`) are wrong for every run so far. The broker's `/admin/spend` exposes `cap_usd` (used by `server.py:648-655`).
- Fix (frontend, S): read the cap from `/api/spend/status` (extend `SpendStatusView`) or from the broker.

### UI-17 (P2, frontend) Two clocks
- The township header clock is `state.hourFrac` (`township-v2.js:5251-5263`), the browser's; the world tick appears only inside the badge text. Every "day N" in the books, "died day N", "built · day N", "unlocks day N" refers to the browser day. An operator reading "Day 3" over a run that never crossed tick 144 is being lied to.
- Fix (frontend, S): when bound, derive `hourFrac` from `LIVE.tick * 10 / 60` (`position.py:48-52` constants) and disable the play/speed controls (they only speed up the fiction).

### UI-18 (P2, world) `/places` on the production host has no geometry; the game's places panel is a hard-coded table
- `agents_api.py:469-493` `/places` returns `{place_id, occupants, occupancy}` only; `location_api`'s richer `/places` (`location_api.py:721-735`, `PlaceSummaryView`) is also mounted on the embedded host but with `map_service=None`; whichever wins the route, `HttpWorldClient.get_places` (`world_client.py:570-619`) expects `tile_x/tile_y` and silently drops every row ("skipping one malformed /places entry"), then returns nothing, or the static `GENESIS_PLACES` (`:141-153`) on 404. Neither page consumes `/api/places` anyway (grep in `static/js`: no caller). DESIGN §8's location viewer (tools usable here, prices, inventory, ownership, history) exists server-side in `location_api.py` (`PlaceDetailView`) and has no UI.
- Fix (frontend, M): a place drawer in the township page fed by `/places/{id}` (through a new `/api/place/{id}` proxy), replacing the modelled `PLACE_ROLES`/`shelfHtml`.

### UI-19 (P2, frontend) Route-order bug: the dynamic `agents.js` route is declared after two middlewares that assume static content
- `server.py:795-829` `version_module_imports` rewrites `import "./x.js"` in every `.js`/`.html` response including the 1.7 MB `agents.js` body (full decode/encode per request), and `no_cache_frontend` (`:831-850`) plus `never_cache_the_page` (`:1015-1037`) set overlapping headers. Three middlewares do one job; the `version_module_imports` regex also matches `src="./…"` inside the cast's JSON strings.
- Fix (frontend, S): one middleware; skip bodies over a size threshold; exempt the generated cast.

### UI-20 (P2, frontend) A Real-sim run and the base pages read different worlds
- `live_world_client()` (`server.py:219-227`) is used by township routes only; `/api/snapshot`, `/api/map`, `/api/places`, `/api/spend`, `/api/agents/{id}` and the relay use `resolved_world_client`. After the button starts a run, `index.html` still shows the process-start world and the WS relay still points at `WORLD_WS_URL`.
- Fix (frontend, S): route all reads through `live_world_client()`; restart the relay pump on rebind.

### UI-21 (P2, docs) Stale docstrings and documentation that a new engineer will follow
- `server.py:30-33` and `build_world_client` (`:90-100`): "boots with… mock_mode=True"; `config.py:9-14`: "MOCK_MODE defaults to True"; `mock_world.py:3-4, 379-396`; `contracts.py:27-52` CONTRACT (`/api/game/snapshot`) never implemented; `game/README.md:25-32` "MockWorldClient stands in"; `world_client.py:37-59` "no `/places`, no map route, no name/bio" (all three now exist on the standalone host); `HANDOVER.md` §1 (file:// works with the full cast - true; served page shows an empty town - not stated), §4.3 badge text, §7.1.2 "MOCK_MODE=true default", §8.3 `MOCK_MODE=false` (now rejected with an error, `config.py:167-171`), §5 "township-v2.js ~3180 lines" (5,481), §5.2 describes the simulation as the feature; `agents.js:1` "250 residents" (314); `township-v2.js:4452-4453` `full_record: 'data/characters/<id>.json'` and `agents.js:3` - `game/static/data/` does not exist (0 files).
- Fix (docs, S): rewrite `HANDOVER.md` §1, §4.3, §5, §7-8 and the four module docstrings against this review.

### UI-22 (P2, frontend) `views.html`/`agents.html` cross-origin defaults and CORS
- Both default to `http://<host>:8100` (`views/api.js:25-27`); the world's routers add CORS headers via `add_cors_headers` on social/governance/agent-viewer/family/skills/workshop routers (`governance_api.py:704` etc.), but `agents_api`/`location_api` do not, and the embedded host has no CORS on `/world/*`. Serving these two pages from `:8400` while the data is on `:8100` also means they cannot see a Real-sim run bound by `realsim.py` (port 8100 is the same, so this one happens to work).
- Fix (frontend, S): proxy `/agent-viewer/*`, `/scenarios*`, `/conversations*`, `/governance/*`, `/voting/*` through `game/server.py` like `/api/town/*`, drop the World-API box.

### UI-23 (P2, tests) Tests that cannot fail or test the wrong thing
- `test_realsim.py:258-267` sets `handle.finished_reason` then asserts it (tautology). `:94-98` unreachable attribute (UI-15). `test_township_zone_bridge.py:112-121` checks `f"'{z}'" in source` - any string literal `'park'` anywhere in `village2.js` satisfies it, so a zone rename would not be caught. `test_game_server.py` exercises the product only through `MockWorldClient` (UI-13). No test loads `township-v2.js` in a DOM (HANDOVER §10 admits a headless stub once hid a `ReferenceError`); `node --check` is the only JS check.
- Fix (tests, M): a Playwright smoke test against `create_app(world_client=FakeWorld)` asserting the badge, sprite count and absence of modelled strings.

### UI-24 (P3, frontend) `talk.js` hard-codes IST (`:55-64`, `+330`); `groupchat.js:29-36` uses `Asia/Kolkata`. Fine for this operator, wrong for the paper's readers; show the world tick and UTC.

### UI-25 (P3, frontend) `part-e.js` and `talk.js` both stamp the resident file (`talk.js:291-337` appends `.authored-strip`; `part-e.js` Skills tab) - two owners of one panel, both re-rendering on independent timers.

### UI-26 (P3, frontend) `shell.js` monkey-patches `window.openModal/closeModal/selectAgent/selectPlace/showEnginePanel` (`shell.js:236-273`) relying on `township-v2.js` being a classic script whose function declarations land on `window`; `live-cast.js:71-74` then looks for `renderScoreboard` etc. on `window` and finds nothing because those are `function` declarations… actually classic-script function declarations are on `window`, `const` bindings are not - the comment at `live-cast.js:66-70` gets this backwards, and `refreshOpenSkillsTab` is explicitly assigned (`township-v2.js:4531`). Fragile; an IIFE or ES module would break the shell silently.

### UI-27 (P3, build) Two generators write `agents.js`
- `build_agents.py` (216 lines, "the only thing that should") and `build_characters.py` (935 lines, "writes … js/township/agents.js") both target the same file; `agents.js:2` names the second. Delete `build_agents.py`.

### UI-28 (P3, dead code) `township.html` + `township.js` + `village.js` (4,469 lines) are a pure-fiction v1 kept "for comparison" and reachable at `/township.html` on the live server; `archive/village3.js` (1,255) is dead; `township-v2.js` carries `wireRunMode`'s `modeMock/modeReal` lookups for buttons that no longer exist (`:1270-1276`), `setRunMode` comments, `renderChat`, `renderHealth`, `renderBuildings`, `renderTreasury`, `renderFood`, `tickConversations`, `LINES`, `EXPERT_KEYS`/Experts tab (rule-based "expert reflection", `:4080-4098, 4390-4414`), `landRegister`, `willOf`. Remove or move to the demo bundle (UI-04).

### UI-29 (P3, frontend) `renderPlaceCard` runs every 400 ms while a place is selected (`:5337`), rebuilding innerHTML and rebinding listeners; `renderGovernment`/`renderWork`/`renderOccupancy` every 400 ms (`:5338-5343`); `renderScoreboard` every 1.2 s over all agents with `balanceNow` per row; `refreshShopSigns` every 1 s. On a 50-agent cast this is fine; the design target of 250 with People cards (`buildRoster`, 250 cards) will not be.

### UI-30 (P3, contracts) `contracts.py` documents a contract nobody implements and `UtteranceRecord`/`RelationshipRecord`/`AgentDetail.recent_utterances` are always empty from the real client (`world_client.py:307-308`). Either fill them from `/world/utterances` and the social router or delete them.

### UI-31 (P3, console) `console.js` `renderPetition` (`:130-179`) offers only `edge` and `depth`; DESIGN §1's expansion "regenerates collision" but the operator has no way to see where the new land is or to name arenas (`arena_names` is in the schema, `schemas.py:118`, never sent). No confirmation dialog on Approve/Disaster/Dials, all irreversible.

### UI-32 (P3, console) Alerts are recomputed from the 2000-entry event ring on every poll (`admin_api.py:1134`, `EventBus.recent()`); a long run loses old alerts and the "crime spike" threshold is over the whole buffer, not a window.

### UI-33 (design) One map for engine and viewer - what it would take (question 2, second half)
Today the township grid is hand-authored art (`village2.js:62-460`) and the engine's map is the Ville JSON; neither can be derived from the other. Options, in increasing cost:
1. **Tiled JSON as the single truth, rendered by the Phaser page** (S-M): fix UI-01/UI-06; the Phaser page already rebuilds layers in place on `world.land.expanded` (`town.js:317-374`) and draws building markers when given coordinates (`:382-423`). Needs: `/map/tiled` proxied, `agent.moved` tiles over WS from the embedded host, `world.building.mapped` payload to carry the footprint (it does: `stamp_building` knows the rect, `service.py:218-291`), and `construction.py` to call `stamp_building` on completion. Cost: the Phaser page has none of the township's books; it would become the "map" and the township books would move beside it.
2. **Township renderer driven by the world's geometry** (L): make `village2.js` build `ZONES`/`buildings` from `/places` (`location_api` sectors + arenas with rects) instead of its own grid, keep its art for known categories, and add `Village.addBuilding(rect, kind)` / `Village.addZone` that rebuild `bIdx`, the walk network and the label layout. `walkNet` (`township-v2.js:830-923`) and `bIdx` (`village2.js:668-673`) are the two caches to invalidate. Coordinates would then be the engine's tiles scaled, and `township_zone_for` becomes unnecessary.
3. **Generate the Ville-side geometry from the township layout** (L, wrong direction): would require re-authoring the Smallville collision/sector tables; not recommended.
Recommendation: (1) now for correctness, then (2) if the isometric look is wanted for the paper; either way `world.building.*` events must carry `{rect, sector_name, kind, status}`.

---

## 4. Gaps vs DESIGN.md §8 (question 3)

| DESIGN §8 item | Exists? | Reads real data? | Notes |
|---|---|---|---|
| Location viewer (who is inside, what they do, tools usable, prices/inventory, ownership, history) | Server: `location_api.py` `/places/{id}` (`PlaceDetailView`, tools = PUBLIC only, `recent_events` from the 2000-event ring). UI: township place card (`township-v2.js:3833-4075`) | Card: people from live positions, activity from world; **roles, shelves/prices, "patients/on duty" from the model**. `/places/{id}` has no UI | Build the drawer (UI-18) |
| World view / governance: charter, active policies, change log with enacting tool call, votes, diff | `views.html` Governance (`views/governance.js`), `/governance/*` mounted on both hosts | Yes (real diff, votes, complaints) | Cross-origin only (UI-22). Township "Government" book contradicts it (UI-04) |
| Voting viewer | `views.html` Voting, `/voting/elections` | Yes | No run has held an election (brief) |
| Scenario / special-event feed with causal trace | `views.html` Scenarios, `social_api.py:401-470` | Only on the standalone host (UI-02) | Categories: crime/trial/birth/death/marriage/disaster/miracle/cult/business/amendment/regime/election |
| Conversations, individual + group, filterable | `views.html` Conversations (`social_api`, standalone only); township Talk + Group chats (`/world/utterances`, `/world/conversations`, both hosts) | Township: yes, live | Township has no place/participant/time filter; views.html has filters but 404s on a run |
| Agent finder (all filters incl. ablation arm, alive/dormant/dead) | `agents.html` Finder, `agent_viewer_api.py:424` | Standalone only (UI-02) | Township roster search is the live substitute but filters on modelled fields (`br`, `occ` after `retrade`) |
| Agent activity viewer (action and why, plan for the day, wallet, health, skills, relationships, utterances) | `agents.html` Activity (standalone only); township inspector `doingBlock` (`:4194-4230`, live) | Township: action + recent tools + skills live; wallet/health/relationships modelled | "Plan for the day" and "why" exist nowhere (perception text and `keep_doing` state are not exposed) |
| Agent reasoning viewer (memories -> prompt -> reasoning -> tool call -> result, model/lane/tokens/cost) | `agents.html` Reasoning (`reasoning.js`, `agent_viewer_api.py:585-600` over `llm_calls.request_body/response_body`) | Standalone only, and only if that host's trace store is the broker's DB | The research centrepiece is unreachable on every run to date |
| "windows, never authorities" | Violated by the township model (UI-03: the viewer decides who is drawn) | | |
| President console | Complete (`admin_api.py`), petition approval grows no map on the production host (UI-01) | | |
| God console | Complete; blessing queue never fills (UI-05); storm/quake are event-only (`admin_api.py:911-914` comment) | | |
| Click any agent -> live conversation, memories, wallet, relationships, trajectory | Township inspector: conversation (Talk), memories = **seeded day-0 memories from the build artefact** (`:4373-4386`), wallet modelled, relationships from genesis, no trajectory | | `recall_memories`/`memory_stream.jsonl` and the trace `memories` table are not surfaced |
| Alert feed | Console Alerts, `admin_api.py:1130-1196` | Yes | See UI-32 |
| Live spend and burn rate | Console spend strip (rate derived client-side), township cost readout from the broker (`server.py:627-672`) | Yes | Cap hard-coded (UI-16) |

---

## 5. Gaps vs the operator's target

- **Thriving, not surviving.** The only live economic figures on screen are `/world/vitals` (treasury, food holdings, town wealth) and the console's framed sentences. No prices over time, no wages, no Gini, no per-agent balance history from the world (the sparkline in the Money tab is the modelled `balanceOn`, `:4419-4424`). What exists to build on: `AgentRow.sim_balance/usd_wallet_cents` per poll; `/family/money-flow`, `/family/wealth`, `/businesses/pnl.by_day` (`agents_api.py:448-467`); `post_price` state in `world.businesses`; the trace `transactions` table (now written, STAKES §4.3a). Add: a `/world/economy` time-series route (per-tick treasury, price board, wage bills, Gini) and a chart panel (dataviz skill) in the township books.
- **Organisations and government running.** Governance view is real; the township Government book is fiction (UI-04). Nothing shows businesses hiring, wages paid/unpaid, tenders, contracts; `/businesses/pnl` has employees and wages but no UI outside the console's sentences. Add a Businesses book reading `/businesses/pnl` and `/world/vitals.built.businesses`.
- **Expansion and construction by the town's own builders.** UI-01/UI-33: nothing on screen. `/world/vitals.built.buildings` lists `kind/owner_id/status` (`vitals.js:62-89`) - that is the only visible trace of construction. Add construction progress (crew, materials consumed, ticks remaining) to `construction.py`'s building record and render it in the place drawer and on the map.
- **Skills/tools that cut model calls.** Part E is the strongest part of the viewer: Skills tab, following step, Workshop adoption figures, Authored book, `following` count on the badge (`server.py:382`). Missing: cost per task over time (tokens per `keep_doing` repetition vs improvised turns); the data is in `llm_calls` + `tool_invocations` joined by `(agent_id, tick)` (STAKES §4.2) and has no route.
- **Supply-demand and ideology-driven mechanics.** No price board UI; the rulebook panel (`/api/rules`) exists only as a data source for the food model. Add a "Prices" book from `world.businesses` posted prices and `market.goods`.
- **Evolving goals.** Nothing surfaces an agent's perception, plan, `keep_doing` commitment, or evolved soul. `run_genesis.py`'s `/world/activity` carries `doing/recent`; the perception text is rendered per tick (`render_perception_text`) and could be exposed per agent; `memory_stream.jsonl` per engine home holds observation/reflection/plan records (SYSTEM_REVIEW §1.1). Add `/world/agents/{id}/mind` (last perception, current continuing action, last N plans/reflections, evolved-soul diff) and a Mind tab replacing the inspector's Memory/Experts tabs.
- **World-class instrumentation.** No run selector, no tick scrubber, no replay: every page reads "now" from a live process; the observatory reads the trace DB but nothing else does. `world/run_report.py` renders after the fact from the live world (its docstring: "generate the report before stopping the run"). Add (a) a `runs` registry (`docs/runs/series/<name>/` already has the DB path and logs), (b) a replay server over the trace DB (`events`, `utterances`, `transactions`, `llm_calls`, `world_snapshots`) exposing the same `/world/*` routes the viewer already consumes at a chosen tick, (c) a tick scrubber in the controls pane bound to the world tick (UI-17).

---

## 6. Recommended work items (prioritised)

1. **P1 - Gate the map on the world, not the browser (UI-03, UI-17).** Spec: when `LIVE.connected`, `agentPosition`/`V.setAgents` ignore `healthSim`/`foodSim`; draw exactly the rows the poll marks `alive`; clock derives from `LIVE.tick`; play/speed controls disabled. Acceptance: headless test with a fake `/api/township/live` (5 alive) and `state.hourFrac += 24*60` asserts 5 sprites and "Day 0 · 13:50" from tick 83.
2. **P1 - Mount the research routers and `/ws/events` on the embedded host (UI-02, UI-06).** Spec: `build_embedded_world_app` gains `social`, `agent_viewer` routers with the runner's trace client, and a `/ws/events` subscriber on `world_state.event_bus`. Acceptance: on a `runseries.sh` world, `agents.html` Finder lists the cast and Reasoning shows a call with `request_body`; `websocat` streams ticks.
3. **P1 - MapService on the embedded host + `/map/tiled` in the game client (UI-01).** Acceptance: approve a petition from the console during a mock series run -> `expansion` non-null, `/api/map` height grows, Phaser page redraws without reload.
4. **P1 - A birth request tool (UI-05).** Acceptance: integration test as in UI-05.
5. **P1 - Split `township-v2.js` into live and demo (UI-04, UI-28).** Spec: `live/` renders only world data (positions, identity, activity, Talk, Group chats, Skills, Workshop, Council, Vitals, Preflight, a Money book from `sim_balance`, a Government book from `/governance/government`); the simulation moves to `demo/` loaded only on `file://`. Acceptance: served page DOM contains none of the modelled strings; `township-v2.js` under 1,500 lines.
6. **P2 - One poller, one snapshot (UI-08).** Acceptance: network trace of an open tab shows <= 1 request/s to `:8400` and one upstream call per world tick.
7. **P2 - Escape all world text (UI-09).** Acceptance: a fake activity line `<img src=x onerror=…>` renders as text in place card, map card, inspector, work tally.
8. **P2 - Proxy the research pages through the game server (UI-22)** and delete the World-API box.
9. **P2 - Observatory in the run scripts (UI-11).**
10. **P2 - Console: cap from the broker, confirm dialogs, arena names (UI-16, UI-31).**
11. **P2 - Boundary and dead code (UI-14, UI-13, UI-27, UI-28, UI-30):** `game -> world` import test; delete `mock_world.py` or implement the Protocol; delete `build_agents.py`, `township.html`+v1 JS, `archive/`.
12. **P2 - Docs (UI-21).**
13. **P3 -> research UI (§5):** economy time-series route + charts; Businesses and Prices books; Mind tab; run registry + trace replay server + tick scrubber.

---

## 7. Open questions for the operator

1. Is the file:// showcase (HANDOVER §1 route A, the 314-resident in-browser town) still a deliverable, or can the simulation be deleted outright? The answer decides UI-04's size.
2. Which host is the production host going forward: keep `--world-mode embedded` and finish it (MapService, routers, WS), or move runs to `world/main.py` plus an external engine supervisor (ROADMAP T3 said the latter)?
3. Should the Phaser page be retired, or become the single map (UI-33 option 1)? Keeping two maps means every construction feature is built twice.
4. Does anything deliver `president/broadcast` and `honors` to agents' perception, or are they event-log-only? (Out of my scope to verify; DESIGN §8 "address-the-nation" implies delivery.)
5. Is `game/realsim.py` (Real-sim button, docker compose, `broker-real`) still wanted? It is the only place the viewer spends money and it is untested end to end.
6. Where should the trace-replay server live: `research/` (read-only against the DB, per CONVENTIONS) or `world/`?
7. The 25-sprite Ville roster and the 18-costume township roster both hash agent ids; the paper needs a stable visual identity per resident across runs - which page is the paper's?
