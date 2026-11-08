# Implementation log — Track A (Sharan)

**What this is:** a running record of what's actually built and verified from `WORK_SPLIT.md`'s Track A, updated as each piece lands. Not a plan — everything below either has passing tests or a captured real-output run behind it.

---

## Status: A1–A7 done and verified. A8–A9 not started (paused for go-ahead).

| # | Item | Status |
|---|---|---|
| A1 | Persona delivery — `pinned_boot.py` wires `ConsciousnessManager` | ✅ Done, tested, simulated |
| A2 | `soul_seed.py` — `build_persona_prompt()` | ✅ Done, tested, simulated |
| A3 | `run_genesis.py` — `RichGenesis` parsing + `persona.txt` writing | ✅ Done, tested, simulated |
| A4 | `social.py` — `say` tool (RC-2) | ✅ Done, tested, simulated |
| A5 | `wiring.py` — utterance tracing | ✅ Done, tested, simulated |
| A6 | `action_costs.py` — `say` cost | ✅ Done, tested |
| A7 | Session log + context-recovery tool | ✅ Done, tested |
| A8 | Life-cycle transition (minor → 18) | ✅ Done, tested, aligned in both townships |
| A9 | Property & land ownership | ✅ Done, tested, aligned in both townships |

## Track B — Denistan: the town, watched and kept alive

| # | Item | Status |
|---|---|---|
| B1 | `supervisor.py` — watchdog restarting FAILED/silent units | ✅ Done, tested |
| B2 | `run_genesis.py` — the runner starts the watchdog | ✅ Done, tested |
| B3 | `family_api.py` — the six section-19 survival views | ✅ Done, tested |
| B4 | `agents_api.py` — business profit/loss over time | ✅ Done, tested |
| B5 | `governance_api.py` — treasury + election traceability | ✅ Done, tested |
| B6 | `skills_api.py` — character sheet, town spread, family tree | ✅ Done, tested |
| B7 | Console “Town” tab + `framing.py` | ✅ Done, tested |
| B8 | `research/demographics.py` — population + bias check | ✅ Done, tested |

## 1. What A1–A6 actually changed

Same file list as `WORK_SPLIT.md`'s Track A table — no deviation:

- `engine/src/sim/pinned_boot.py` — one new argument (`consciousness_mgr=`) on the existing `Coordinator(...)` call, plus the code that reads `persona.txt` and registers it as a STABLE prompt section before the frozen engine's own generic-identity path can fire.
- `app/world/life/soul_seed.py` — new `build_persona_prompt(agent)`.
- `app/world/run_genesis.py` — `seed_genesis()` now parses `RichGenesis` (persona-carrying) instead of the base `Genesis`; `start_agents()` writes each agent's `persona.txt` before its engine process spawns.
- `app/world/tools/primitives/social.py` — new `say` tool, with the block-delivery guarantee (a tool call only ever fires with a complete, decided utterance — never a partial stream) written directly into the handler's docstring.
- `app/world/wiring.py` — `TraceStore.record_utterance_if_new()`, the missing write path into the `utterances` table.
- `app/world/ledger/action_costs.py` — `say` priced (SOCIAL, 1 sim flat).
- `engine/src/sim/session_log.py` (new file) — `SessionLogWriter`, `read_recent_turns()`, `render_recovery_context()`, `build_session_log_tool()`, `install_session_log_permissions()`. See §6 below.
- `engine/src/sim/pinned_boot.py` — wires the session log in: creates a `SessionLogWriter` per engine, attaches it to the emitter, registers the `recall_session_log` tool, grants it a permission rule, and calls `session_log.start_turn()` at the top of `send_tick()`.

## 2. Test results

```
app/  (world/tools/tests/test_social.py, world/life/tests/test_soul_seed.py,
       world/tests/test_social_api.py)              30 passed
app/  (full suite: world/ broker/ game/ research/
       supabase/ tests/)                             1690 passed, 7 deselected
engine/ (tests/test_sim_pinned_boot.py)               9 passed
```

No regressions. `smallville/` (the vendored Ville map assets DESIGN.md §1 requires, gitignored, not tracked) was re-vendored locally to run the map-dependent tests.

## 3. The real-output simulation — what "verify it works" actually looked like

**Important and honest up front:** there are no AWS credentials configured anywhere on this machine (no `~/.aws`, no `AWS_*` env vars, no `aws` CLI). That means a genuine call to Kimi K2.5 on Bedrock is not possible from here right now, regardless of model ID or ARN. Everything below is real *except* the model's own words, which are a scripted stand-in for what a live model would return — clearly labeled as such everywhere it appears.

### Part 1 — a real agent's real persona reaches a real system prompt on the wire

Pulled agent `a0009` ("Priya Corrigan") straight from `app/world/genesis_50.json`, ran the real `build_persona_prompt()` + `build_soul_seed()`, wrote the result to a real `persona.txt`, and booted the real `boot_sim_engine()` code path against it:

```
Persona text length: 2860 chars (verbatim from the real generator)

--- reply the engine returned for this tick (scripted stand-in for Kimi) ---
I say to whoever's nearby: "Prices at the market moved again this
morning. If anyone's short, come find me before sundown."

--- what the ACTUAL wire request to the model contained ---
model requested: moonshotai.kimi-k2.5
system prompt length sent on the wire: 2860 chars
contains 'Priya Corrigan': True
contains her real hard limit ('will not put a thumb on the scale'): True
contains THE LAW OF KASUKABE block: True
exact match to PERSONA_TEXT substring present: True
```

`model requested: moonshotai.kimi-k2.5` is real, not scripted — it's the actual, hardcoded, guarded model id this sim topology only ever resolves to (`src.sim.model_profile.MODEL_ID`), confirmed by the real request body the fake broker captured. The persona text — her name, her literal hard limit, her secret, the full law-of-the-town block — is present character-for-character on the wire, not summarized or dropped.

### Part 2 — a real `say` call, dispatched through the real world, heard by a real listener

Ran two real agents (`a0039`, `a0040`) through the real `world.wiring.build_world_server()` composition root, moved them to the same location, and dispatched a real `say` tool call:

```
--- the actual row now sitting in the real utterances table ---
{
  "speaker_agent_id": "a0039",
  "listener_agent_id": "a0040",
  "conversation_id": "7c69c892-0ad1-4177-bf27-b7759f19e7dd",
  "location": "cafe",
  "content": "Good morning, Ulrike Greenhow. Have you heard about the market prices today?",
  "is_public": false,
  "tick": 0
}

--- Ulrike Greenhow's own perception this tick ---
visible_agents: ['a0039']
heard_events count: 3
  - type=agent.utterance.spoken involves_self=True
    content='Good morning, Ulrike Greenhow. Have you heard about the market prices today?'
```

`involves_self=True` on the listener's own perception is the whole point: the receiving agent genuinely perceives the complete utterance, whole, on her own next turn — through the real perception system, not a mock of it.

### Part 3 — a fuller town: 5 real agents, real personas, a real morning

Same real pipeline, more agents at once. Pulled 5 real working-age agents straight out of `world/genesis.json` (Ezra Lund, Ulrike Greenhow, Saoirse Quarshie, Sebastian Petrov, Jovana Ravensworth), moved them all to the cafe, and ran a real sequence:

```
1. Ezra Lund (public): "Morning, all. Prices at the market moved again..."
2. Sebastian Petrov -> Ezra Lund: "Appreciate the tip... I'll hold off selling today."
3. Jovana Ravensworth -> Saoirse Quarshie: transfer 400 sim, memo="covering you till payday"
   Jovana Ravensworth balance: 5000 -> 4600
   Saoirse Quarshie balance: 5000 -> 5400
4. Ulrike Greenhow (present, never directly addressed) perceived:
   heard: "Morning, all. Prices at the market moved again..." (involves_self=False)
   heard: "Appreciate the tip, Ezra Lund..." (involves_self=False)
```

Both utterances landed in the real `utterances` table; the transfer landed a real double-entry row in `transactions` (`from_agent_id: a0043, to_agent_id: a0041, amount: 400, reason: transfer`). Ulrike — present at the same location but never spoken to directly — still perceived both public utterances (`involves_self=False`, correctly), which is exactly the location-based "overhearing" behavior the perception system is supposed to provide.

**One real finding surfaced while building this:** `world.wiring.build_world_server()` (the dev/test composition root, distinct from `run_genesis.py`'s production `GenesisRunner`) loads `genesis.json` via the plain `Genesis` model, not `RichGenesis` — so `server.genesis.agents[i].persona` isn't populated there. This doesn't affect the real boot path (`pinned_boot.py` reads `persona.txt` off disk, a separate contract `GenesisRunner.start_agents()` already writes correctly — see A3), but it's worth knowing: if Track B's dashboard work (`family_api.py`) ever wants persona data through `server.genesis` directly, it will hit the same gap and need the same one-line fix A3 already made in the other composition root.

## 4. What's needed to go from this to a genuine Kimi-generated reply

Nothing in the code — the guard rail already only ever resolves to Kimi K2.5. The single blocker is real AWS credentials (an access key/secret, an SSO profile, or equivalent) configured on whatever machine actually runs the broker with `MOCK_MODE=false`. Once that exists, the exact same `boot_sim_engine()` path proven above would carry real model output instead of the scripted stand-in — nothing else changes.

## 4a. Where is demographic simulation?

**Not built yet, and not part of Track A.** Per `WORK_SPLIT.md`, the demographic/research module (The 250 §24 — measuring the population as a group, not just one agent at a time, and checking for bias by age/sex/occupation/household shape) is item **B8, `app/research/demographics.py`, assigned to Track B (Denistan)**. It also genuinely needs behavioral data from agents actually living and deciding things over many ticks to be meaningful — which itself needs A1–A6 (this track) working first, plus a real run.

What already exists today, and is real (not simulated) — the genesis population's own census, pulled live from `world/genesis.json`:

```
Total population: 250

Age bracket breakdown:
  minors: 38 (15.2%)
  working-age: 174 (69.6%)
  elderly: 38 (15.2%)

Sex breakdown:
  male: 125 (50.0%)
  female: 125 (50.0%)

Occupation breakdown (working-age):
  day_laborer: 23      trader: 22           artisan: 21
  miner: 19             farmer: 19           office_administrator: 15
  forester: 14          college_instructor: 14   government_clerk: 14
  construction_worker: 4   college_student: 3
  construction_company_owner: 1   pharmacist: 1   grocery_store_owner: 1
  barista: 1             cafe_owner: 1        grocery_clerk: 1
```

This is the *composition* of the town — real, and available right now. What §24's demographic module actually measures is *behavior* broken down against this composition (do miners vote differently from traders, is wealth distributed evenly across sexes, does bias creep in) — that requires agents to have actually acted, which requires a real or realistically-mocked run, which is why it's sequenced after both tracks land real mechanics, not before.

## 5a. A7 — session log + context-recovery tool

The 250 §32 / `AGENT_LIFE_FRAMEWORK.md` §32: every engine process writes one structured JSON line per turn (`input`, `reasoning`, `actions`, `done_status`), and can read its own recent history back through a real tool call — the same mechanism serves both a freshly-restarted process recovering context and a still-running agent that's drifted off its own train of thought (§32.3).

**What it is, concretely:**
- `SessionLogWriter` subscribes to the engine's `EventEmitter` (a documented pub/sub seam on the frozen `src/engine_core/events.py` — `.on(callback)`, not an edit to that file) and accumulates `ThinkingDelta` and `ToolExecution` events between `start_turn()` (called right before `coordinator.handle_input()`) and the `ExecutionSummary` event the frozen engine itself fires when a turn completes — that summary is what triggers the actual flush to `<SEPTEMBER_HOME>/<agent_id>/session_log.jsonl`.
- `recall_session_log` is a `ToolDef` registered into each engine's own `ToolRegistry` (the same pattern `world_tools.py` uses for `world:*` tools) — an agent can call it mid-conversation and get back a rendered summary of its last N turns.

**A real bug found and fixed while testing this:** registering the tool alone was not enough for the model to actually call it. The frozen `thread.py`'s `check_permission()` runs on every tool call regardless of how it was registered, and denies by default unless an explicit `PermissionRule` allows it — exactly the rule `world_tools.py` already grants `world:*` tools via `WORLD_TOOL_ALLOW_RULE` / `install_permissions(thread)`. Without an equivalent rule for `recall_session_log`, a denied call returns "Permission denied: ..." text and skips the handler entirely — including the `ToolExecution(phase="result")` event, so the call looked like it fired but silently never ran. Fixed the same way `world_tools.py` does it: `SESSION_LOG_TOOL_ALLOW_RULE` + `install_session_log_permissions(thread)`, called from `pinned_boot.py` right after the existing `world_tools.install_permissions(thread)` line — no change to the frozen file, just the same seam already in use.

**Tests** (`engine/tests/test_sim_pinned_boot.py`):
- `test_session_log_records_a_plain_turn_with_all_four_fields` — a plain text-only turn still writes one complete JSONL record (all four fields present).
- `test_session_log_records_a_tool_call_and_the_recall_tool_reads_it_back` — a two-turn run where turn 2's model genuinely calls `recall_session_log`, and the result written to the log contains turn 1's real recorded input, proving the recall path actually reads back real prior history, not a stub.

Both pass; `engine/tests/test_sim_pinned_boot.py` is now 9/9 (was 7/7 before A7).

## 5. Note on A9 (property & land)

Before writing any code, checked the existing codebase first and found a `Plot` model (`world/tools/models.py`) already exists for construction/development rights — but nothing in production code ever creates one, and the land-petition module's own docstring says outright that actually growing the map is "a separate, not-yet-built module's job." A9's design will build the general-purpose 1×1 tile-ownership system additively, without touching or duplicating that half-built `Plot` machinery — flagged here so it's a known, deliberate decision, not an oversight.

---

*September · internal research · Track A implementation log · updated 2026-08-20*


## 6. A8 — life-cycle transition (The 250 §26)

`aging.py` already capped a minor's age at 18, cleared the life record's
`sponsor_parent_id` and fired `agent.aged_to_adult`. What it could not do was
the half with consequences, and both halves were genuinely missing:

**The wallet.** Nothing in the simulation ever cleared `Account.is_minor`.
`Ledger.resolve_usd_payer` reads exactly that flag, so an account opened with
`is_minor=True` billed its parent for the rest of the run — a former minor who
had crossed eighteen game-hours ago was still, financially, a child.

**The decision.** Nothing happened to the household at all.

| File | Change |
|---|---|
| `world/ledger/core.py` | `Ledger.end_parent_billing()` — synchronous, idempotent, moves no money |
| `world/life/ports.py` | `GuardianshipPort` — separate from `EconomyPort` so no existing implementer grows a method it has no use for |
| `world/tools/economy_bridge.py` | `LedgerGuardianshipPort` |
| `world/life/household.py` | `ComingOfAgeChoice`, `come_of_age()`, `leave_household()` — §26's three futures and nothing else |
| `world/tools/primitives/social.py` | the `come_of_age` tool, gated at `min_age=18`, so the decision is the agent's own on its own turn |
| `world/ledger/action_costs.py` | one SOCIAL row, priced alongside `marry` |

`PAIR_OFF` delegates to `marry()` rather than reimplementing it — §25's
newlywed shape, one generation later. Ordering follows `marry`'s own
discipline: the fallible external calls happen before any record is rewritten.

**Tests:** `world/life/tests/test_coming_of_age.py` (8), `world/ledger/tests/test_end_parent_billing.py` (6).

## 7. A9 — property and land ownership (The 250 §31)

| File | Change |
|---|---|
| `world/tools/models.py` | `OwnerClass`, `TileOwnership`, `Bequest`, `Will` |
| `world/tools/state.py` | `tile_owners`, `wills`, `tile_key()`, `owner_of_tile()`; both added to `OWNED_STATE_FIELDS` |
| `world/tools/primitives/property.py` **(new)** | `transfer_tile`, `auction_land`, `set_will`, `read_will`, plus `settle_property_estate()` |
| `world/tools/bootstrap.py` | registered |
| `world/ledger/action_costs.py` | four FEE rows |

Three decisions worth recording:

- **No third "unowned" class.** §31.2 says a private tile is never ownerless,
  so unclaimed land *is* government land. `require_tile()` materialises that
  reading on demand rather than pre-seeding a row for every tile on the map.
- **Land is keyed `"x,y"`, not by tuple.** The checkpoint round-trip turns
  dict keys into JSON object keys; a tuple key would come back as the string
  `"(142, 87)"` and never match a lookup again.
- **Auction proceeds go to the treasury**, not the seat-holder's wallet —
  §31.2's "the same way a tax or a fee already does".

**Tests:** `world/tools/tests/test_property.py` (15), against the real wired world.

## 8. Both townships, aligned

The same two rules now run in the viewer, on the same constants:

- **A8** — minors age at DESIGN.md §4's 1 year per game-hour; at eighteen the
  age chart reclassifies them, the bill moves off the parent, they take up a
  trade (§26: "this is where the town's occupational roster actually gets
  filled over time"), and they stay / move out / pair off.
- **A9** — the land register splits into government and private units, every
  dwelling is held by the household living in it, and each resident has a will
  or falls back to the nearest living relative.

Surfaced in a new **Estate** tab on the resident file, plus four rows in the
Government panel and three new standing rules. Identical in `township.js` and
`township-v2.js`.

Observed over one run: 38 genesis minors, all eighteen by game-hour 18; choices
split 20 / 14 / 4; the working-age roster grows 238 → 276; nine trades take on
new entrants; 80 wills on file; 374 government and 533 private land units in
v2 (3,504 / 4,977 in v1's larger map), with no unit ownerless.


## 9. Track B, B1–B5

### B1 — the watchdog (§33)

`UnitState.FAILED` and `Supervisor.restart()` both already existed; nothing
ever joined them. `SupervisorWatchdog` in `infra/firecracker/supervisor.py`
polls `list()` and restarts on two conditions, because §33 names both:

- **FAILED** — the unit reported its own death.
- **Gone silent** — still claims RUNNING but `last_health_check_at` is stale.
  This is the one that matters: a wedged engine looks perfectly healthy to
  anything that only reads `state`.

`UNHEALTHY` is deliberately *not* restarted on sight — that is what a unit
looks like mid-boot or during a slow broker call. Exponential backoff, a
restart budget, and a give-up state keep a unit that fails on startup from
becoming a restart storm. **18 tests.**

### B2 — and a second gap it exposed (§33)

§33 says both Supervisor protocols "already define… a `restart(unit_id)`
method". True of `infra/firecracker`, **not** true of `run_genesis.py` — and
that is the backend the sim actually boots on. Neither `ProcessSupervisor`
nor `MockSupervisor` had one, so there was nothing for the watchdog to call.
Added `restart()` to the runner's protocol and both backends (owning the
stop-then-start ordering itself, since `start()` raises on a live unit), then
B2's one line.

A second trap, caught by a test rather than in production: `run_genesis`
declares its **own** `UnitState`, so `status.state is UnitState.FAILED`
across the two enum classes is always `False`. The watchdog would have
silently never restarted anything on the process backend. It now compares by
value. **8 tests.**

### B3 — `world/family_api.py`, the six §19 views

`/family/agents`, `/family/households`, `/family/at-risk`, `/family/wealth`,
`/family/tree/{id}`, `/family/money-flow`. Runway is computed through
`world.life.upkeep.compute_household_bill` rather than re-derived, so a
change to the upkeep constants moves the watchlist too. Money flow reads the
ledger's own `Transaction` rows — the record the $1000 invariant is checked
against — not the event bus. **20 tests.**

### B4 — business profit/loss (§23)

`/businesses/pnl` and `/businesses/{id}/pnl`, broken out by day so a failing
business is visible before it vanishes, with a plain-language `trend`
(`declining`, `cannot make payroll`, `insolvent`). A new query, not a new
ledger: nothing recorded, nothing mutated. **12 tests.**

### B5 — treasury and term traceability (§23)

`/governance/treasury`, `/governance/policy-and-treasury` (the rate and the
balance it feeds, together), `/governance/terms`. A term runs from its
election's tally tick to the next one's, so an amendment's own `enacted_tick`
places it in exactly one term without a new field or table. An amendment
enacted before any election belongs to no term and is dropped rather than
attributed to a PM who was not yet in office. **10 tests.**

### The memory pipeline (prerequisite, done before B1)

`engine/src/sim/memory_stream.py` + `TraceStore.record_memory_if_new()`. The
`memories` table had been queried by `agent_viewer_api.py` since genesis and
never written to by anything. **14 tests.**

**Totals: 82 new tests across Track B and the memory pipeline. `app/` is 1654
passing** (excluding `infra/firecracker`'s pre-existing `AF_UNIX path too
long` failures and `supabase`'s live-DB suite, neither touched by this work).


## 10. Track B, B6–B8

### B6 — `world/skills_api.py` (§22)

`/skills/agents/{id}` (character sheet), `/skills/town` (who is best at
what), `/skills/families` and `/skills/families/{id}` (which trades run in
which families).

Two gaps had to close first. `world.skills` stored **levels only**, so
"self-taught vs. taught by whom" — half of what §22 asks a character sheet
to show — was unanswerable from world state; `skill_sources` now records it,
written by `teach_skill_handler` *only when the lesson actually raised the
student's proficiency*, because being taught something you already know
better does not make you someone's student. And the `skills` table was a
third case of the `utterances`/`memories` pattern — schema since genesis,
queried by `agent_viewer_api.py`, never written; `record_skill_if_new()`
closes it.

What a skill is *worth* is not invented here: §22 asks for it "measured as
how much cheaper this agent's work is, next to a novice", and that is
`apply_skill_discount` — the same function the ledger charges through, so
the sheet can never claim a discount the agent would not get. **15 tests.**

### B7 — the console's Town tab (§19.7, §22, §23)

`/api/town/overview`, `/api/town/households`, `/api/town/wealth`,
`/api/town/terms`, plus the tab itself in `index.html`/`console.js`/
`console.css`.

The framing lives in **`game/console/framing.py`**, not in JavaScript, for
one reason: a confidence label is a claim, and a claim needs a test. A
dashboard that says "high confidence" on two days of data is worse than one
showing a bare number. `HIGH_CONFIDENCE_DAYS = 10` because §19.7's own
example rests on "the last 10 days of income data", and the overview derives
days-observed **from the books** rather than assuming — claiming a
confidence without knowing how long the town has run is exactly the failure
that section warns against. **21 tests.**

**A wiring gap found here:** `family_api` and `skills_api` existed but
`world/main.py` mounted neither, so B3 and B6 were dead code. Both are now
mounted — 15 routes that were unreachable are now served.

### B8 — `research/demographics.py` (§24)

`slice_population()`, `bias_report()`, `household_outcomes()`,
`emergence_check()`. Pure functions over flat records, so the same code
measures a live `WorldState`, a Parquet export, or a fixture.

The design decision worth recording: **the report never claims to know
whether a disparity is a finding or the design working.** The elderly being
poorer is the pension design; women being poorer, when no mechanic in the
codebase reads sex, is a finding. `MECHANICALLY_EXPECTED` marks the former
so it is *reported but not flagged* — flagging it would train an operator to
ignore the flag. Slices under `MIN_SLICE_SIZE` are excluded: the papers'
bias checks rest on large samples, and a slice of two is noise that would
bury real findings. **18 tests.**

---

**Track B totals: 122 tests across B1–B8. `app/` is 1708 passing** —
excluding `infra/firecracker`'s pre-existing `AF_UNIX path too long`
failures (8 occurrences, unrelated to this work) and `supabase`'s live-DB
suite.


## 11. Wiring audit — Track A, Track B, and the seams between them

Asked of every piece: *is this reachable at runtime, or does it merely
exist?* A module nothing imports, an event nobody subscribes to, and a
route no host mounts all pass their own unit tests and are all still
broken. Five real breaks were found and fixed.

| # | Break | Effect before the fix |
|---|---|---|
| 1 | `MemoryStreamWriter` was constructed with **no sink** | The stream wrote a file on the engine's disk and stopped. `TraceStore.record_memory_if_new` could never fire — producer and consumer both passed their unit tests while completely disconnected. |
| 2 | `settle_property_estate` (A9) was **called by nothing** | A dead owner's land stayed in their name forever. |
| 3 | `agent.aged_to_adult` (A8) had **no consumer** | A minor crossing eighteen changed nothing: the parent kept funding their cognition, the household never moved. |
| 4 | The genesis runner's embedded app mounted **neither family, skills, nor governance** | The runner is the only host that boots in production, so B7's console tab 404'd against a real run even though `world/main.py` served it. |
| 5 | B2 imported `infra/` from `world/` via a `sys.path` insert | Violated `infra/README.md`'s stated boundary: *"Never imported by `world/`, `broker/`, `game/`, or `research/`."* |

**Fixes.** (1) A `POST /agents/{id}/memories` intake on the world host
publishes `agent.memory.recorded`; `WorldClient.record_memory` is the
engine-side sink, and it never raises — a dropped memory row is a gap in
the record, a dropped turn is a dead agent. Deliberately not a tool:
recording what you just did is not a decision, and a tool would let a model
skip or falsify it. (2) `apply_death` now settles land — spouse, then
children, then parents, then escheat, the same order money follows — and a
death can never fail because the register objected. (3) The life tick
brings a new adult of age. The *choice* stays the agent's (`come_of_age` is
a registered tool); what the tick guarantees is the floor — parent billing
ends at eighteen whether or not the agent ever thinks about it. (4) Both
hosts now mount the same read surface. (5) The watchdog was moved into
`run_genesis.py`, mirroring exactly how that file already duplicates the
Supervisor protocol for the same documented reason.

**New guards** (`world/tests/test_track_wiring.py`, 15 tests): both hosts
serve all 11 town routes; `world/` never imports `infra/` (a `sys.path`
insert counts); the two watchdogs make the **same decision on nine
identical inputs**, so a deliberate duplicate cannot quietly become two
different watchdogs; a memory posted by an engine reaches the bus; a dream
probe is refused at the door; the persona file is written before the engine
boots and read after; and **every registered tool has a cost row** —
including A8's and A9's.

Two of my own test expectations were wrong and the code was right: a
resident must already be 18 when `come_of_age` runs (`advance_age` caps
first), and a **spouse inherits before a child** — nearest living relative,
exactly as §31.3 specifies.

**Totals: `app/` 1723 passing, engine sim 29 passing.** The only failures
anywhere remain `infra/firecracker`'s pre-existing `AF_UNIX path too long`
(a socket-path limit in this checkout's directory depth) and `supabase`'s
live-DB suite.
