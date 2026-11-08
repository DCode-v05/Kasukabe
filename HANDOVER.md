# Kasukabe — engineering handover

A 314-resident town simulation, built as a research instrument: every
resident is an LLM-backed agent with a wallet, a family, a job, a memory
stream and a cause of death. The question the whole thing exists to answer
is **which agents survive, and why**.

This document is the entry point for someone picking the repo up cold. It
covers what the system is, what is finished, what is not, what is known to
be broken, how to run it, and exactly what stands between here and a live
run against real model credentials.

*Last verified: 2026-08-21. Test suite at time of writing: **1469 passing**
(`world/` + `game/`).*

---

## 1. Quick start — running the village map (v2)

The v2 map is the finished one. It computes all 314 residents, their
families, jobs, wages, homes, illnesses, deaths and the town economy **in
the browser**, from `world/genesis.json`. It needs no API key, no AWS
account, no database and no world server.

There are two ways to run it. Try A first — it takes about five seconds.

### The exact files, from the repo root

| Path | What it is |
|---|---|
| `app/game/static/township-v2.html` | **The page you open.** |
| `app/game/static/js/township/village2.js` | The renderer. |
| `app/game/static/js/township/township-v2.js` | The simulation and every panel. |
| `app/game/static/js/township/agents.js` | The 250 genesis residents. |
| `app/game/static/css/township.css` | Styling. |

### A. Straight off disk — no install, no server

`township-v2.html` is deliberately self-contained: the cast is bundled as a
JS constant (`agents.js`) rather than fetched, precisely so the page opens
from the filesystem. **Double-click the file, or:**

```bash
# macOS
open  app/game/static/township-v2.html

# Linux
xdg-open app/game/static/township-v2.html

# Windows (PowerShell)
start .\app\game\static\township-v2.html
```

Your browser will show a `file:///.../app/game/static/township-v2.html`
URL. Everything works: the map, search, place cards, profile cards, zoom,
roofs-off, night. The header badge reads `SIMULATED SCHEDULE` and live mode
switches itself off, because there is no server to poll.

### B. Through the game server — needed for live engine positions

Use this when you want §4.3's live mode, or the other pages.

```bash
# 1. from the REPO ROOT, go into app/ - this is where pyproject.toml lives,
#    and `uv` will not find the project from anywhere else
cd app

# 2. install (creates app/.venv from uv.lock)
uv sync

# 3. serve. Run this from app/, not from the repo root.
uv run uvicorn game.server:app --port 8400
```

Then open exactly this URL:

```
http://127.0.0.1:8400/township-v2.html
```

> **The one URL mistake everyone makes.** The static files are mounted at
> `/`, **not** at `/static/` — see `app/game/server.py`'s
> `app.mount("/", StaticFiles(...))`. `http://127.0.0.1:8400/township-v2.html`
> returns 200; `http://127.0.0.1:8400/static/township-v2.html` returns **404**.
> There is no `/static` prefix anywhere.

### The other pages on the same server

| URL | What it is |
|---|---|
| `/township-v2.html` | **The final map.** Volumetric isometric, 314 residents. |
| `/township.html` | v1 of the map — flat isometric, kept for comparison. |
| `/index.html` | The Phaser client that renders the *engine's* Ville map. |
| `/agents.html` | Agent finder / activity / reasoning viewers. |
| `/views.html` | Research views. |

### Requirements

- **Route A:** a modern browser. Nothing else.
- **Route B:** Python ≥ 3.12 (3.12.5 verified) and
  [`uv`](https://docs.astral.sh/uv/). Node is only needed if you want to run
  the JS syntax checks (`node --check`).

### URL parameters (useful for demos and bug reports)

`township-v2.html` accepts two, on either route:

| Parameter | Effect |
|---|---|
| `?t=17.4` | Start the clock at that hour. `2.05` = 02:03, the town asleep. |
| `?speed=0` | Start paused. `1`, `6`, `20` also work. |

Example: `http://127.0.0.1:8400/township-v2.html?t=17.4&speed=0` — the
evening commute, frozen.

### If it does not work

| Symptom | Cause and fix |
|---|---|
| `404 Not Found` in the browser | You used `/static/township-v2.html`. Drop the `/static`. |
| `Address already in use` | Port 8400 is taken. Use `--port 8401` and adjust the URL. |
| ``error: No `pyproject.toml` found in current directory or any parent directory`` | You ran `uv` from the repo root. `cd app` first. |
| `ModuleNotFoundError: No module named 'game'` | Same cause — `uvicorn` was started outside `app/`. |
| Page loads, header and buttons appear, **map area is blank** | A JavaScript exception in the render loop. It does not show a dialog — open the browser console (F12). See §10. |
| Badge says `SIMULATED SCHEDULE` | Expected unless you have done §8.3. It is not an error. |

## 2. What this system is

Five independently-runnable services, one repo.

```
kasukabe/
├── app/                        # everything that runs
│   ├── world/                  #   the simulation itself (port 8100)
│   ├── broker/                 #   the ONLY thing that talks to Bedrock (8200)
│   ├── game/                   #   the viewer + its server (8400)
│   │   └── console/            #   the operator console (8300)
│   ├── research/               #   offline measurement over the trace store
│   ├── infra/                  #   deployment: Firecracker, Caddy, systemd, runbook
│   └── tests/                  #   cross-cutting boundary tests
├── engine/                     # the vendored agent engine (see §3, FROZEN)
├── smallville/                 # vendored Ville map assets (Apache-2.0)
├── infra/                      # ops paperwork: quota request, launch workflows
├── docs/                       # this file, and archived artifacts
└── *.md                        # the specifications — see §2.2
```

### 2.1 The five services

| Service | Port | Run with | What it does |
|---|---|---|---|
| **world** | 8100 | `make run-world` | The simulation: ticks, agents, ledger, map, tools. |
| **broker** | 8200 | `make run-broker` | Bedrock gateway. Six lanes, rate limits, spend cap, trace store. **The only package allowed to `import boto3`.** |
| **game** | 8400 | `make run-game` | Serves the viewer and proxies world state to it. |
| **console** | 8300 | `make run-console` | Operator console — solvency, at-risk, treasury, skills. |
| **genesis runner** | — | `python -m world.run_genesis` | Boots 250 agent engines and drives the whole run. |

### 2.2 The specifications (do not move these)

These live at the repo root and are cited **by name and section number from
inside the source code** — `DESIGN.md` alone is referenced by **239 source
files**. Moving or renaming them silently invalidates those citations.

| File | What it is |
|---|---|
| `DESIGN.md` | The system specification. Sections are cited throughout the code. |
| `ROADMAP.md` | Delivery plan. **Law 1** lists the frozen directories. |
| `WORK_SPLIT.md` | The A1–A9 / B1–B8 task split this round was built to. |
| `COGNITION_DELIVERY.md` | How persona, memory and speech reach the engine. |
| `AGENT_LIFE_FRAMEWORK.md` | "The 250" — the life-cycle spec (§19–§33). |
| `IMPLEMENTATION_LOG.md` | Running log of what was built, in order. |
| `RESEARCH_NOTES.md` | Research framing and the papers the design follows. |
| `app/STATUS.md` | Pre-existing risk register and dry-run results. |
| `app/CONVENTIONS.md` | Module boundaries. Read before touching imports. |

---

## 3. The rules the code enforces on itself

These are not style preferences. Each has a test that fails if broken.

1. **`ROADMAP.md` Law 1 — frozen directories.** `engine/src/engine_core/`,
   `engine/src/memory/`, `engine/src/herald/`, `engine/src/learning_centre/`
   are vendored and must not be edited. Extend them through the sanctioned
   seams instead:
   - the **EventEmitter pub/sub** (`.on(callback)`) — how
     `engine/src/sim/session_log.py` and `engine/src/sim/memory_stream.py`
     attach without touching frozen code;
   - **`PermissionRule` / `thread.session_rules`** — how a tool is granted
     (`check_permission` denies by default).
   Changeable: `engine/src/sim/*`, `app/world/*`, and the tool registry.

2. **`game/` never imports `world/`.** The viewer is a presentation layer.
   Where it needs a constant from `world/` it *copies* it with a comment
   naming the source (see `game/position.py`). Enforced by
   `app/tests/test_module_boundaries.py`.

3. **`infra/` is never imported by `world/`, `broker/`, `game/` or
   `research/`.** A `sys.path` insert counts as an import. This is why
   `run_genesis.py` carries its own local copy of the supervisor watchdog
   with a parity test rather than importing `infra`'s.

4. **`boto3` lives only in `broker/`.** Nothing else may import it.

5. **The $1000 ledger invariant.** Every `Ledger.move()` re-checks that the
   town's total USD is unchanged. It is structurally unbreakable.

---

## 4. How agent movement works (both halves)

This is the part most recently completed, and the part most likely to be
misunderstood, because **there are two different maps**.

### 4.1 The two maps, and why they cannot share coordinates

| | The engine's map | The township map |
|---|---|---|
| Where | `smallville/` assets, loaded by `world/map/` | generated in JS by `game/static/js/township/village2.js` |
| Shape | The Ville — Smallville's town, ~140×100 tiles | A procedurally generated 104×104 town |
| Addressed by | `TileCoord(x, y)` + sector/arena names | its own tile grid + district ids (`hospital`, `docks`, …) |
| Who walks it | `world/core/movement.py` | the viewer |

They are **unrelated tile spaces**. An engine tile cannot be plotted on the
township map. The honest bridge between them is the **named place**, not the
coordinate — see `game/position.py`'s `township_zone_for()`.

### 4.2 (b) Engine-side — agents genuinely walk, tick by tick

The chain, end to end:

```
move tool                  world/tools/primitives/movement.py
  └─ starts a route on     world/core/movement.py :: MovementSystem.start_journey()
       advanced each tick  world/core/engine.py   :: TickEngine.tick() -> movement.advance()
       emits               agent.moved {agent_id, location_id, tile_x, tile_y, arrived}
  └─ recorded by           world/tools/bootstrap.py :: attach_tile_tracking()
       into                world.agent_tiles[agent_id] = (x, y)
  └─ served as             world/agents_api.py :: AgentRow.tile_x / tile_y
  └─ consumed by           game/world_client.py -> game/server.py :: /api/snapshot
```

**What was broken:** `MovementSystem` was constructed by
`world/tools/bootstrap.py` and handed to the `move` tool, but **neither
composition root passed it to `TickEngine`**, so `advance()` was never
called. Every route a `move` started froze on its first tile forever:
`world.agent_tiles` never updated, `tile_x`/`tile_y` never changed, and the
only positions anything downstream ever saw came from `move_handler`'s
instant-jump fallback. Agents teleported.

**What was done:** `movement_system=` is now passed at both roots —
`world/run_genesis.py` (~line 1886) and `world/wiring.py` (~line 1149).
`MovementSystem` is `None` when the Ville assets are absent, which
`TickEngine` already treats as "no movement wiring", so nothing regresses.

**Guarded by** `world/tests/test_track_wiring.py`:
- `test_both_composition_roots_hand_movement_to_the_tick_engine` — parses
  both modules' ASTs and asserts the keyword is present at every
  `TickEngine(...)` call site.
- `test_a_started_route_advances_one_tile_per_tick` — starts a real journey
  on the real map, ticks the engine, and asserts both the journey and
  `world.agent_tiles` actually moved. **Passes, does not skip.**

### 4.3 (a) Viewer-side — real positions, animated in real time

In `game/static/js/township/township-v2.js`, under the `LIVE MODE` banner.

- Polls `GET /api/township/live` every 2 s.
- Each row is `{id, zone, location_id, action, alive, dormant}`. `zone` is a
  township district id, resolved server-side by `township_zone_for()`.
- When the engine reports a resident **somewhere new, the viewer does not
  teleport them**. It starts a real walk along the same street network every
  scripted commute uses (`walkRoute` over `walkNet()`) and animates it frame
  by frame over wall-clock seconds, scaled by distance and by the clock
  speed so a live resident keeps pace with a scripted one at 6×.
- A resident redirected mid-walk carries on from where they had actually got
  to (`livePointOf`), not from where they started.

**It degrades to the built-in schedule in every failure case** — no world
server, a 502, a mock world whose ids don't match genesis, or a location
this town has no district for. That is why `township_zone_for()` returns
`None` rather than guessing a district: a wrong guess would teleport someone
across town every poll.

The header badge always says which mode you are in — **`LIVE ENGINE · n of
314 bound`** or **`SIMULATED SCHEDULE`**. A viewer that quietly falls back
is a viewer you cannot trust.

**Verified:** with a feed carrying real genesis ids, 250 of 314 residents
bind (the other 64 are township-born children who are not in
`genesis.json`); reassigning every bound resident to a new district put
**250 of 250 mid-walk**, none standing in water. Against the current
mock-backed endpoint, `bound=0` and the town runs its own schedule exactly
as before.

**Guarded by** `game/tests/test_township_zone_bridge.py` (21 tests),
including a test that every sector name the bridge keys on still exists in
`world/map/genesis.py` — so a rename upstream fails loudly instead of
silently unbinding everyone.

---

## 5. The township map (v2) in detail

Three files, all under `app/game/static/`:

| File | Lines | Role |
|---|---|---|
| `js/township/village2.js` | ~1750 | **The renderer.** Terrain, buildings, interiors, props, vehicles, animals, people. Owns nothing about the simulation. |
| `js/township/township-v2.js` | ~3180 | **The simulation + every panel.** Clock, behaviour, economy, health, cards, search, live mode. |
| `township-v2.html` + `css/township.css` | — | Page and styling. |
| `js/township/agents.js` | — | The 250 genesis residents, generated. |

Regenerate the cast after editing `world/genesis.json`:
```bash
cd app/game/static && python3 js/township/build_characters.py
```

### 5.1 What the renderer draws

- **2:1 isometric**, painter's-algorithm depth sort, sprites baked to
  offscreen canvases and cached, viewport culling.
- **Layout**: 6 road bands, a 104×104 grid, a river held east of town, and
  outlying farmland / pasture / orchard / forest / ore outcrop / docks.
- **Interiors** (`ROOM_PLAN` / `KIT` / `furnishRoom`): beds, sofas, desks
  with laptops, ward beds, cells, counters, quay stores. Toggle with
  **Roofs off**.
- **Residents**: 18 costume kinds (police cap, doctor's coat, miner's hi-vis
  and helmet, chef's toque, scrub cap, mortarboard…), a striding walk cycle,
  a reclining pose for sleepers and ward patients, and a **zoom-aware
  figure scale** — full 2× when the whole town is fitted, easing to life
  size (~⅓ of a cottage's height) close up.
- **The government, findable**: the Prime Minister and the four ministers
  (`seatHolders()`) wear the sash of office and carry a map pin — a gold star
  for the PM, a purple dot for a minister — so the people who run the town can
  be picked out of a crowd. They are searchable by office ("prime minister"),
  and their seat is named wherever they appear, including when the PM is a
  patient in the hospital.
- **Street furniture**: 96 lamps that light at night, 1185 trees, 384
  flowering bushes, benches, stalls, boats, and 40 roaming dogs/cats/
  squirrels that each keep to the ground type they belong on.

### 5.2 What the simulation does

Runs entirely in the browser at `SEC_PER_HOUR_AT_1X = 18` (1× is an
unhurried walk; 6×/20× are what make it quick).

- **Routing**: every commute is a real path over `walkTiles` (carriageway +
  pavement), via a BFS field per district. Nobody straight-lines across
  gardens.
- **Worksites**: occupations are dealt across sub-sites in exact proportion
  (farmers split 55/27/18 across farmland/pasture/orchard, and so on) so no
  district sits empty.
- **Shifts**: hospital, police and fire run 24/7 on a rota, with a real
  hour's commute either side.
- **Life cycle**: ageing, coming of age, marriage, birth, illness, death,
  wills and inheritance — the township mirror of the Track A rules.
- **Economy**: wages, upkeep, tax, household aggregation. **Only working-age
  people are ranked** for wealthiest/poorest; newborns and the retired are
  excluded, and ranking is family-first then individual.
- **Government**: funds 7 initiatives cumulatively as the treasury allows
  (3 by day 10, 5 by day 30, all 7 by day 60) and posts a two-officer guard.

### 5.3 The map UI

Google-Maps-shaped: drag to pan, scroll or **+/−** to zoom, **Fit whole
town**, **Fullscreen**, **Labels**, **Roofs off**, **Night**, **Export PNG**.

- **Search** (top-left of the map) finds people *and* places — "river" finds
  River Docks, "oak" finds both Oak Hill College and the Oak family.
- **Click a building** → a place card listing everyone inside, grouped by
  role. The hospital shows on-duty doctors, on-duty nurses and patients by
  name, age and what each is doing. Same for police, fire, bank,
  government, school, college, docks, pasture and orchard.
- **Click a resident** → a profile card: activity, workplace and shift,
  health, marriage, home, kin count, wallet, wage/upkeep/tax, skill unlock,
  traits.

---

## 6. Status — what is done

### Track A (`WORK_SPLIT.md` §1) — complete

| | What | Where |
|---|---|---|
| A1 | `ConsciousnessManager` wired into `Coordinator`, persona as a STABLE section | `engine/src/sim/pinned_boot.py` |
| A2 | `build_persona_prompt(agent)` | `app/world/life/soul_seed.py` |
| A3 | `RichGenesis` parsing + `persona.txt` per agent before spawn | `app/world/run_genesis.py` |
| A4 | The `say` tool | `app/world/tools/primitives/social.py` |
| A5 | `record_utterance_if_new()` | `app/world/wiring.py` |
| A6 | `SCHEDULE_TABLE["say"]` | `app/world/ledger/action_costs.py` |
| A7 | Per-turn 4-field session log + read-back tool | `engine/src/sim/session_log.py` |
| A8 | Coming of age: parent billing ends, agent chooses | `app/world/life/household.py`, `app/world/ledger/core.py` |
| A9 | Tile ownership, land auction, wills, estate settlement | `app/world/tools/primitives/property.py` |

Plus the memory stream (`engine/src/sim/memory_stream.py`) — observation /
reflection / plan records with importance scores, after Park et al.,
*Generative Agents* — sunk into the trace store via `wiring.py`.

### Track B (`WORK_SPLIT.md` §2) — complete

| | What | Where |
|---|---|---|
| B1 | `SupervisorWatchdog` — restarts a unit the instant it goes `FAILED` | `app/infra/firecracker/supervisor.py` |
| B2 | Watchdog started by the runner (own local copy — boundary rule 3) | `app/world/run_genesis.py` |
| B3 | Six family/solvency/at-risk/wealth/money-flow views | `app/world/family_api.py` |
| B4 | Business profit and loss over time | `app/world/agents_api.py` |
| B5 | Treasury + election-outcome traceability | `app/world/governance_api.py` |
| B6 | Skill views: per agent, town-wide spread, family tree | `app/world/skills_api.py` |
| B7 | All of it rendered, with confidence framing | `app/game/console/app.py`, `framing.py` |
| B8 | Demographic slices + bias check | `app/research/demographics.py` |

### Wiring audit — 6 breaks found and fixed

`world/tests/test_track_wiring.py` (17 tests) exists because a module that
is imported by nothing, an event nobody subscribes to and a route no host
mounts all pass their own unit tests and are all still broken. It found:

1. the memory stream wrote a file but never reached the world event bus;
2. `settle_property_estate` (A9) was called by nothing — a dead owner's land
   never moved;
3. `agent.aged_to_adult` (A8) had no consumer — nobody ever came of age;
4. the genesis runner's embedded app — the only host that boots in
   production — mounted neither the family, skills nor governance routers,
   so the console's Town tab 404'd against a real run;
5. `world/` imported `infra/`, violating boundary rule 3;
6. **movement was never advanced** (§4.2);
7. **the memory stream had no reader.** `MemoryStreamWriter` recorded every
   observation, reflection and plan, and the only way to read them back was
   `read_memory_stream()` — a Python function the console and the tests could
   call and the agent could not. Unlike `session_log`, it had no `ToolDef` and
   no `PermissionRule`, so a live agent would have formed memories it could
   never retrieve. That is a log file, not a memory stream. Closed by
   `recall_memories` in `engine/src/sim/memory_stream.py`, registered and
   granted at `engine/src/sim/pinned_boot.py`. Five tests in
   `engine/tests/test_sim_memory_stream.py` cover the reader, the permission
   grant (a registered tool with no rule is silently never callable), and the
   fact that the composition root actually wires it.

---

## 6b. What agents can actually do on their own

This is the section that matters for a live run, because it answers one
question: **when a resident reasons from its own memory and decides to do
something, is there anything stopping it?**

The short answer is that the tool list is not the limit. An agent that wants
to do something the primitives do not cover can **invent the tool for it**.

### 6b.1 How a thought becomes an action

Each tick, for the agents the scheduler says are due:

```
TickEngine.tick()                     world/core/engine.py
  └─ perception built for each due agent      core/perception.py
  └─ rendered to text                         world/retrieval_depth.py
       render_perception_text(depth=…)        — ambient, who is visible,
                                                what was heard, relationships
  └─ sent to that agent's own engine          run_genesis.py :: dispatch_due_agents
       AgentGateway.send_tick(...)            → engine/src/sim/agent_adapter.py

  the agent, inside its own process:
    ├─ persona is a STABLE prompt section     engine/src/sim/pinned_boot.py (A1)
    ├─ recall_memories        ← its own observations, reflections, plans
    ├─ recall_session_log     ← what it did on recent turns
    ├─ 42 tool schemas        ← everything below
    └─ picks tools and calls them

  each call → POST /agents/{id}/tool_calls    run_genesis.py
            → ToolRegistry.dispatch()          world/tools/registry.py
               · alive / not dormant
               · location gate ("you must be at the mill to mill")
               · funds, permits, ownership
               · cost charged to the agent's own wallet
  └─ the turn's events become memory again    engine/src/sim/memory_stream.py
       observation / plan / reflection, importance-scored
```

The loop closes: what an agent did becomes what it remembers, and what it
remembers is retrievable on any later turn. **That closure was broken until
this pass** — see §6's wiring audit item 7. Nothing else in the chain was.

### 6b.2 The 42 tools every agent is handed

Verified live against a booted runner: `GET /tools/registry` serves **42 tool
schemas** to every agent. All are priced in `world/ledger/action_costs.py`
(zero unpriced) and all are permitted by a single wildcard,
`PermissionRule("world:*")` in `engine/src/sim/world_tools.py` — the engine's
own HITL permission system deliberately gets out of the way, because the world
server is the authority on whether a call is allowed.

| Module | Tools | What a resident can do |
|---|---|---|
| `social.py` | `say`, `marry`, `pray`, `teach_skill`, `come_of_age` | Talk to anyone in earshot, marry, worship, pass on a skill, choose a life at 18 |
| `movement.py` | `move` | Walk anywhere, tile by tile (§4.2) |
| `places.py` | `look_around`, `describe_place`, `list_places` | Find out where they are and what is here |
| `resources.py` | `harvest`, `mine`, `fell_timber` | Take from the land |
| `production.py` | `craft`, `cook` | Make things |
| `commerce.py` | `buy`, `sell`, `transfer`, `store_item`, `withdraw_item` | Trade, give, use storage |
| `civic.py` | `petition`, `vote`, `file_complaint`, `register_org`, `register_business`, `transfer_business`, `hire_employee`, `fire_employee` | Demand, vote, complain, organise, employ |
| `property.py` | `transfer_tile`, `auction_land`, `set_will`, `read_will` | Own land, bid for it, leave it to someone |
| `construction.py` | `build_structure` | Build |
| `justice.py` | `file_subpoena`, `grant_subpoena`, `deny_subpoena` | Compel testimony, and refuse |
| `crime.py` | `steal` | Break the rules, and be caught |
| `meta.py` | `define_tool`, `define_business_tool`, `define_organization_tool`, `approve_patent`, `reject_patent`, `revoke_patent` | **Invent new tools** — see below |

Plus, inside the agent's own process: `recall_memories` and
`recall_session_log` (`engine/src/sim/`).

### 6b.3 The part that makes the environment open-ended

`world/tools/meta.py` implements DESIGN.md section 2's meta-tool. An agent
that wants to do something no primitive covers can compose one:

- **`define_tool`** — files a new tool as a fixed sequence of primitive calls
  at the government patent office. Costs a fee. Starts `approved=False` and is
  **not callable**. A sitting minister must `approve_patent` it before anyone
  can use it; it can be `reject_patent`ed, and later `revoke_patent`ed.
- **`define_business_tool` / `define_organization_tool`** — the trade-secret
  path. No patent office, no fee, no ministerial review; registers immediately,
  scoped to the owner and their granted employees. A patent trades disclosure
  for protection; a trade secret trades protection for secrecy.

So a guild that invents a better way of working can keep it to itself, or file
it and let the whole town use it — and the government can take it away.

**"No new physics" is true by construction, not by policy.** Three mechanisms,
all covered by `world/tools/tests/test_meta_tool.py` (7 tests):

1. `ToolRegistry.primitive_names` is **frozen** by `bootstrap.py` after the
   built-ins register and before `define_tool` is added. Every step of an
   invented tool is checked against that frozen set — so an invented tool can
   never cite another invented tool, and `define_tool` is not in the whitelist,
   which makes a meta-tool that composes meta-tools *unrepresentable*.
2. Filing never makes a tool callable. Only `approve_patent` — GOVERNMENT
   namespace, `ActorRequirements(require_government_seat=True)`, gated at the
   patent office — calls `registry.register()`. That requirement is held by
   exactly five residents: the Prime Minister and the four ministers
   (`seatHolders()`), who are the sashed, pinned figures on the map (§5). Who
   holds a seat therefore decides what the whole town is allowed to invent —
   which is why they are worth being able to find.
3. The composite handler runs no arbitrary code. It replays the filed steps
   one at a time through `ToolRegistry.dispatch()` — the same gate-checked path
   every ordinary call takes — **under the inventor's identity and wallet**, not
   the caller's.

An invented tool is data: a list of primitive calls plus placeholder
substitution. That is what lets the answer to "can an agent do anything?" be
*yes, within the physics*, without the physics being negotiable.

### 6b.4 What is genuinely not possible

Worth stating plainly, so nobody expects behaviour the environment cannot
produce:

- **No new primitives at runtime.** An agent cannot invent flight, or a way to
  create sim from nothing. The $1000 ledger invariant is re-checked on every
  `Ledger.move()`.
- **No acting at a distance.** Location gates apply to invented tools exactly
  as to primitives — you must be at the mill to mill.
- **No free actions.** Every tool costs, charged to the agent's own wallet, so
  an agent that runs out of money runs out of options. That is the mechanism
  the survival question turns on.
- **No `strike` primitive.** A strike is reachable with what exists — organise
  with `register_org`, coordinate with `say`, state the demand with `petition`,
  and withhold labour by simply not calling `harvest`/`mine`/`craft`/`cook`/
  `fell_timber` (there is no generic `work` tool that must be called). An agent
  could even `define_tool` a "join_work_stoppage" composite. What is missing is
  not the capability but the **record**: nothing labels that pattern as a
  strike, so no view can show one. If a named mechanic is wanted, the honest
  shape is a tool that registers a labour dispute against a business and
  surfaces in `family_api` and `businesses/pnl` — not a new way to stop working.

## 7. Status — what is *not* done

Ranked by what would block a live run.

### 7.1 Blocking a live research run

1. **No live run has ever been executed against real Bedrock.** Everything
   below §6 was proven under mock inference, unit tests and a 2-game-day
   dry run. The GO/NO-GO checklist is `app/STATUS.md` §"GO / NO-GO".
2. **`bound=0` against the current world server.** The game server defaults
   to `MOCK_MODE=true` → `MockWorldClient`, 12 mock agents whose ids
   (`mock-agent-000`) match no genesis resident. Live mode is *correct* and
   *verified*, but it will not light up until a real world server is
   pointed at it (§8.3).
3. **Only 250 of 314 residents can ever bind.** `genesis.json` holds 250;
   the township's other 64 are children born in the browser sim. Either
   births must round-trip to `world/`, or the viewer must accept
   engine-authored births.

### 7.2 Known gaps carried from `app/STATUS.md`

4. **Charter/governance state is not checkpointed.** A resume re-seeds a
   fresh genesis charter rather than restoring exact governance state.
   Elections beyond the first are not auto-advanced.
5. **`infra/firecracker/`'s real-hardware path is untested on real KVM.**
   The fake-backend path (32 tests) is the real coverage. Use
   `--supervisor-backend process` until that is exercised.
6. **`world/app.py` is a health-only scaffold**, superseded by
   `world/main.py`, kept only for its own test.
7. **Most of `DESIGN.md` §10's research instruments** — dream probes,
   ablation arms — were out of scope and are unevaluated.

### 7.3 Known rough edges in the map

8. **The township sim and the engine sim are two separate simulations.**
   The browser computes its own economy, health and life cycle. Live mode
   overrides *position* only. Reconciling the two — or making the browser a
   pure view — is the single biggest design decision left.
9. **The location bridge is deliberately partial.** `township_zone_for()`
   covers the sector names and slugs that exist today; a business at an
   unmapped `location_id` returns `None` and stays scripted.
10. **The temple has no township district** and maps to the town square.
11. **`village3.js`** (v1's organic layout in v2's 3D) is archived at
    `app/game/static/js/township/archive/` and has not been kept in step.

---

## 8. Running the whole system

### 8.1 Environment setup

```bash
git clone <this repo> && cd kasukabe/app
uv sync                      # creates .venv from uv.lock
uv run pytest                # expect 1469 passing in world/ + game/
```

Requirements: **Python ≥ 3.12** (3.12.5 verified), **uv**, and — only for
the microVM path or `start-genesis.sh` — **Docker**.

### 8.2 Local bring-up, mock inference, no credentials

Four terminals, from `app/`:

```bash
make run-world      # :8100  the simulation
make run-broker     # :8200  Bedrock gateway (mock mode)
make run-game       # :8400  the viewer
make run-console    # :8300  the operator console
```

Then open <http://127.0.0.1:8400/township-v2.html>.

### 8.3 Making the map show *live* engine positions

The map defaults to `SIMULATED SCHEDULE`. To bind it to a real world:

1. Boot the real world server with the genesis population:
   ```bash
   cd app && uv run uvicorn world.main:app --port 8100
   ```
2. Point the game server at it instead of the mock:
   ```bash
   MOCK_MODE=false WORLD_BASE_URL=http://127.0.0.1:8100 \
     uv run uvicorn game.server:app --port 8400
   ```
3. Reload `/township-v2.html`. The badge should read
   **`LIVE ENGINE · n of 314 bound`**.

If it still says `SIMULATED SCHEDULE`, check
`curl localhost:8400/api/township/live` — the response carries `connected`,
`bound` and a `detail` string saying exactly what failed.

### 8.4 A real genesis run with live model credentials

**Credentials.** Cognition runs on **AWS Bedrock**, and `broker/` is the
only package that may touch it. There is no `OPENAI_API_KEY`-style secret —
it is the standard boto3 credential chain. Put real credentials in
`~/.aws/credentials`; `start-genesis.sh` mounts that directory read-only
into the broker container.

**Models are not configurable.** `broker/config.py` hardcodes
`MODEL_ALLOWLIST = (moonshotai.kimi-k2.5, deepseek.v3.2)` across
`BEDROCK_LANE_REGIONS = (ap-south-1, us-east-1, us-west-2)` — six lanes at
100 RPM each, 600 RPM aggregate. That file's own comment calls it "a law,
not a deployment knob". Any other model id is a hard 403.

**Launch:**
```bash
cd app
BUDGET_CAP_CENTS=38000 ./start-genesis.sh     # $380 cap
```

That script starts the broker with `MOCK_MODE=false` and then the genesis
runner with `--live --world-mode embedded --supervisor-backend process`.

**Operator knobs** (these *are* env-configurable): `SPEND_CAP_USD`,
`SPEND_WARN_FRACTION`, `CIRCUIT_BREAKER_THRESHOLD`,
`CIRCUIT_BREAKER_COOLDOWN_S`, `TRACE_SQLITE_PATH`, `WORLD_BASE_URL`,
`AUTH_SHARED_SECRET`, `OPERATOR_TOKEN`.

**Watch it:** the console at :8300, and the spend line in the map header.

---

## 9. Next step — a live run that answers "who survives?"

This is the sequence to get from here to the actual research result.

### Step 1 — prove the loop for one game-day, cheaply

Run the genesis runner in **mock mode** first (`--live` omitted). This
exercises the identical tick loop, checkpointing, metrics and spend governor
without spending a cent. Confirm: checkpoints written, no watchdog restarts,
ledger invariant held, trace store populated.

### Step 2 — bind the map to the real world

Do §8.3 against that mock run. You should see the badge go green and
residents walking to engine-chosen destinations. **Do this before spending
money** — it is the cheapest possible test of the whole chain.

### Step 3 — smoke the credentials on one lane

Boot only the broker with `MOCK_MODE=false` and a `SPEND_CAP_USD` of 1.00.
Send a handful of turns. Confirm a lane is chosen, tokens are counted,
`llm_calls` rows carry `request_body`/`response_body`, and the spend
governor trips at the cap.

### Step 4 — the real run

```bash
BUDGET_CAP_CENTS=38000 ./start-genesis.sh
```

Budget: at roughly $79/hr across six lanes, $380 buys about 4½ hours of
wall clock. Compute is on top.

### Step 5 — measure survival

This is the output. The instruments already exist:

| Question | Where to get it |
|---|---|
| Who died, when, of what? | `world/agents_api.py` + the trace store's `agents` / event rows |
| Which households ran out of money first? | `world/family_api.py` → `/family/at-risk`, sorted by runway |
| Which businesses failed? | `world/agents_api.py` → `/businesses/pnl` |
| Did survival correlate with age/sex/occupation/household shape? | `research/demographics.py` → `slice_population`, `household_outcomes` |
| Is that correlation real or mechanical? | `research/demographics.py` → `bias_report`, `emergence_check` against `MECHANICALLY_EXPECTED` |
| What did an agent actually think before it died? | `agent_viewer_api.py` reasoning view, over `llm_calls.request_body` |

**The one thing to add first:** `emergence_check` exists but has never been
run against a real trace. Its whole purpose is to separate *"agents behaved
this way because they reasoned their way there"* from *"agents behaved this
way because the rules made it inevitable"*. A survival result without it is
not a research finding.

### Step 6 — the honest caveat to record

The township map's economy and the engine's economy are **two separate
simulations** (§7.3.8). Survival numbers must come from the **engine's**
ledger and trace store, never from the browser. The map is an instrument for
watching, not a source of results.

---

## 10. Working on this code

```bash
cd app
uv run pytest                              # everything
uv run pytest world/ game/ -q              # the 1469
uv run pytest world/tests/test_track_wiring.py -q   # the wiring audit
node --check game/static/js/township/village2.js    # JS syntax
```

**Verifying the map in a browser** is not optional — a Node/DOM-stub harness
that loads the files but never calls `Village.mount()` renders nothing and
reports a false pass. That exact false pass once hid a `ReferenceError` that
had blanked every building on the page.

```bash
# a real screenshot — note: NO --virtual-time-budget, it hangs on the rAF loop
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --hide-scrollbars --no-sandbox \
  --enable-logging=stderr --v=0 \
  --screenshot=/tmp/shot.png --window-size=1600,1150 \
  "http://127.0.0.1:8400/township-v2.html?t=12.5&speed=0"
```

`?t=` sets the start hour, `?speed=0` pauses. Console errors appear on
stderr — **always check them**; a thrown exception in the render loop shows
up as a blank map, not as an error dialog.

`township-v2.js` is a classic script with no IIFE, so its top-level names
(`AGENTS`, `liveAgentPos`, `runSearch`, `selectPlace`, `initiativesOn`,
`LIVE`) are reachable as bare identifiers from an injected script — but not
as `window.x`.
