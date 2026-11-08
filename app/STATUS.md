# Kasukabe convergence status

Written by the convergence pass that reconciled the eight concurrently-landed
tracks (wire-world, broker-lanes, namespaces, persistence, genesis-runner,
host-bringup, firecracker, game-console), ran the full suite in Docker, ran
a 250-agent 2-game-day mock dry run, and fixed every real cross-track
mismatch it found. Dated against DESIGN.md as of 2026-07-31.

## TL;DR

**GO** for a live 250-agent genesis run's core mechanics (world state, ledger,
checkpoints, pause/resume, spend governor) - all independently proven below,
not just unit-tested in isolation.

**RESOLVED 2026-08-17. Risk #1 below is closed and this NO-GO no longer
applies.** DESIGN.md section 4 was updated on 2026-07-31, after this file
was written, to six lanes at 600 RPM aggregate, explicitly superseding the
2026-07-30 single-model note. The broker's topology was correct all along.
Round 1 then ran on 2026-08-01 across all six lanes. Sections 9 and 11 of
DESIGN.md, which this file also flagged as stale, were corrected on
2026-08-17. Risk #1's original text is kept below as the record of what the
convergence pass found; read it as history, not as a live blocker.

The original finding was: the broker's actual model/region topology (2 models
x 3 regions = 6 lanes, 600 RPM aggregate, DeepSeek included) contradicts
DESIGN.md section 4's "final decision 2026-07-30" (Kimi-only, ap-south-1
only, 100 RPM total). This is not a bug I can silently pick a side on - it
changes what gets studied, what gets spent, and which of two documents is
wrong. Someone with authority over DESIGN.md needs to say which one is real
before the first dollar is spent.

The operator President/God console (`game/console/`) cannot currently
command a live world server at all - see Risk #2. It is not required for
the simulation to run, only to watch and steer it live.

## What I did

1. Reconciled interfaces across all eight tracks' landed code (world/,
   broker/, game/, research/, supabase/, infra/) and fixed three real,
   previously-unexercised cross-track shape mismatches - see "Fixes made"
   below.
2. Ran the full suite in Docker: **1393 passed, 7 skipped, 0 failed**
   (`app/` suite) + **32 passed, 1 skipped, 0 failed** (`infra/firecracker/`'s
   own suite, real hardware gated). 1425 total passing, 8 skipped (all
   correctly gated: live-DB migration tests with no DB, and the real-KVM
   Firecracker integration test on a Mac with no `/dev/kvm`).
3. Ran a real 250-agent, 288-tick (2 game-day), mock-inference dry run
   through `world.run_genesis.GenesisRunner` directly (not a toy - the same
   class a live run uses), proving: the ledger invariant held after every
   single tick, checkpoints round-trip through gzip-JSON on disk, a
   process restart resuming from a checkpoint reproduces an uninterrupted
   run bit-for-bit, and the spend governor actually stops the run when the
   budget cap is lowered. Full output in "Dry run results" below.
4. Wrote this file.

## Fixes made (real bugs, not style)

All three were found because I proved things end to end instead of trusting
that green unit tests meant the seams were exercised - none of these were
caught by the 1393 passing tests, because each track's tests mocked out the
other side of the seam that broke.

### 1. Life-domain events never reached `LocationIndex` (dead agents stayed "present" forever)

`world/tools/economy_bridge.py`'s `life_event_to_world_event()` folded
`LifeEvent.agent_id` into the `target_agent_id` payload key only (for
perception interrupts). `world/core/perception.py`'s `LocationIndex.attach()`
separately requires a plain `agent_id` payload key for `agent.moved` /
`agent.spawned` / `agent.died`. Every life-domain event convert-and-published
this way (aging, health, marriage, and critically **every natural death**)
silently failed that check and logged a warning - confirmed live: the first
dry run produced ~250 lines of `agent.aged_to_adult event missing a string
agent_id payload field, ignored by LocationIndex` spam. The real
consequence: `run_genesis.py`'s own `apply_death()` publishes `agent.died`
through this exact path, so **a dead agent was never removed from the
location index** - `LocationIndex.remove()` never fired, meaning dead
agents would still count as "present" at their last location for perception
and `agents_at()` purposes for the rest of the run.

Fix: `life_event_to_world_event()` now sets both `target_agent_id` and
`agent_id` in the payload. Verified with a standalone repro (construct a
`LocationIndex`, place an agent, publish a converted `agent.died` event,
assert `location_of()` is `None` afterward) - confirmed broken before the
fix, confirmed fixed after. Re-ran the dry run afterward: zero warnings.

File: `app/world/tools/economy_bridge.py`.

### 2. Court subpoenas and the tool-invocation audit log did not survive a checkpoint/resume

`world/tools/state.py`'s `OWNED_STATE_FIELDS` (the namespaces track's
addition) includes `subpoenas` and `tool_invocations` - DESIGN.md section
2's "complete causal record" and court-subpoena mechanism. But
`world/run_genesis.py`'s `WorldStateCheckpointAdapter` (the genesis-runner
track, written against an earlier version of `OWNED_STATE_FIELDS`) only
knew about eight of the ten owned dict/list fields. `snapshot()` silently
dropped `subpoenas`/`tool_invocations` from every checkpoint, and
`restore()` silently left them at `build_world()`'s fresh empty defaults
since `WorldState.restore()` is a plain `for name, value in
snapshot.items(): setattr(...)` - no error anywhere, just quiet data loss.
**Every process restart during a live run would have wiped every court
subpoena and the entire tool-invocation audit log** - the exact artifact
DESIGN.md's crime/justice research (false-conviction-rate metric) and the
ACL system's "still logged for researchers regardless of secrecy" guarantee
depend on.

The 288-tick dry run's pause/resume check did not catch this on its own
(no subpoenas were filed in a 2-day mock run with zero crimes), so I wrote
a second, targeted repro: seed one `Subpoena` and one `ToolInvocationRecord`
into a real `WorldState`, snapshot it, restore into a *fresh* `WorldState`
(matching what `boot(resume=True)` actually does), assert both survive.
Confirmed broken before the fix, confirmed fixed after.

Fix: added `Subpoena` to `WORLD_STATE_DICT_MODEL_TYPES` and
`ToolInvocationRecord` to `WORLD_STATE_LIST_MODEL_TYPES`, with `.get(name,
{})`/`.get(name, [])` fallbacks in `restore()` so a checkpoint written
before this fix still loads (matching the existing
`RunnerCountersCheckpointAdapter` legacy-tolerance pattern already in the
same file).

File: `app/world/run_genesis.py`.

### 3. The real world server was never wired into local bring-up

`world/main.py` + `world/wiring.py` (the wire-world track's actual,
complete FastAPI composition - map, ledger, charter, tools, crime,
President/God routes, `/ws/events`) was built but its own module docstring
said outright: "Not wired into ... Makefile's/docker-compose.yml's
`world.app:app` - those two files belong to whoever owns the deploy
config." `make run-world` and `docker compose up world` were still
launching `world/app.py`, a scaffold that has only ever returned
`{"status": "ok"}` on `/health` and nothing else. Nobody running the
documented bring-up commands was reaching the real server.

Fix: `Makefile`'s `run-world` and `docker-compose.yml`'s `world` service
now launch `world.main:app`. `docker-compose.yml`'s `world` service also
needed the `../smallville:/smallville:ro` mount the `test` service already
has - `WorldConfig()` defaults to `require_map=True` and
`build_world_server()` raises a `RuntimeError` without it (the error
message itself names this exact compose file as the fix). Verified for
real: built the `world` Docker image, ran `docker compose up world`, hit
`GET /health` and `GET /state/summary` - real server, real 250-agent
genesis population, `usd_total_cents: 100000` balanced ledger, `halted:
false`. Tore it down clean. Updated both `world/main.py`'s and
`world/app.py`'s docstrings so they stop describing the old (now wrong)
wiring.

Files: `app/Makefile`, `app/docker-compose.yml`, `app/world/main.py`,
`app/world/app.py`.

## Test suite: full result

```
cd /Users/septemberai/Vat/kasukabe/app
docker compose build test && docker compose run --rm test
```

```
1393 passed, 7 skipped, 1 warning in ~24s
```

The 7 skips are `supabase/tests/test_supabase_migrations_apply_live.py`,
correctly gated on a live Postgres connection that does not exist in this
environment (SQLite/WAL is the actual production trace store per
CONVENTIONS.md - these tests exercise an alternate path that is not this
project's system of record). The 1 warning is FastAPI's own
`starlette.testclient` deprecation notice, unrelated to this codebase.

`infra/firecracker/` ships its own separate Docker test harness
(deliberately not merged into the shared `test` service - see that
directory's own `docker-compose.yml` comment, written while another track
was actively editing the shared one):

```
cd /Users/septemberai/Vat/kasukabe/app/infra/firecracker
docker compose build test && docker compose run --rm test
```

```
32 passed, 1 skipped in ~4s
```

The skip is the real-hardware integration test, correctly gated on
`FIRECRACKER_REAL_HW=1` + `/dev/kvm` (neither present on this Mac).

**Nothing was skipped, deleted, or weakened to get here.** Both suites
were fully green before I started; I only added two standalone repro
scripts (not part of either suite, run manually, see "Fixes made" above)
to prove the two bugs I found and fixed were real, then confirmed the
existing suites stayed green afterward.

## Dry run results (250 agents, mock inference, 2 game-days)

Ran directly against `world.run_genesis.GenesisRunner` in the `test`
Docker image, using the real `world/genesis.json` (250 agents, $500
genesis wallets + $500 treasury). Not part of pytest; a one-off
integration proof per the convergence task's own instructions.

**Scenario A - full run, ledger invariant + checkpoint round-trip:**
288 ticks (144 ticks/game-day x 2), budget cap $400 (generous, not meant
to trip).

```
agents=250
ran 288 ticks, final tick_index=288
invariant_failures=0
checkpoints written: ['checkpoint_0000000144.json.gz', 'checkpoint_0000000288.json.gz']
final ledger total=100000 (expect 100000)
final burned=3720c
deaths=0 births=0
checkpoint checkpoint_0000000144.json.gz: tick=144 day=1 ledger_total=100000 agents_in_snapshot=250
checkpoint checkpoint_0000000288.json.gz: tick=288 day=2 ledger_total=100000 agents_in_snapshot=250
checkpoint round-trip ok=True
```

The ledger's own `check_invariant()` is structurally called on every single
`move()` (per `world/ledger/core.py`'s own design - it is impossible to
move money without it), so this is really proof the invariant is
unbreakable by construction, exercised for real across 288 ticks x 250
agents rather than trusted from a docstring. Zero deaths/births in this
window matches DESIGN.md's own stated incidence rates (terminal death
0.08%/agent/day is genuinely rare over 250 agents x 2 days) and births
being operator-gated (nothing auto-grants one).

**Scenario B - pause/resume bit-exact:** ran a continuous reference to
tick 164 (144 + 20, crossing the day-1 checkpoint boundary) in one process.
Separately, ran a first process to tick 144 (writing the checkpoint) and
killed it, then booted a **brand-new `GenesisRunner` instance** with
`resume=True` and ran it 20 more ticks from the restored checkpoint.

```
resumed at tick=144, running 20 more ticks
reference tick=164 resumed tick=164
bit-exact match=True
```

"Bit-exact match" compares the full ledger (every account's USD/sim
balance, closed status), every agent's age/alive/health/location, deaths,
and births between the two runs - not just the ledger total. Zero diffs.

**Scenario C - spend governor trips at a lowered cap:** budget cap set to
50 cents (vs. the $400 default), same 250 agents, run for up to 288 ticks.

```
tripped=True reason='spend 58 cents reached the 50-cent budget cap'
after 38 ticks, burned=58c (cap=50c)
```

Stopped cleanly at tick 38 of 288, `governor.tripped is True`, no exception
escaped `run_burst()`.

```
ledger invariant held every tick (288 ticks, 250 agents): True
checkpoint round-trip ok: True
pause/resume bit-exact: True
spend governor trips at lowered cap (50c): True (stopped after 38/288 ticks, burned 58c)
ALL CHECKS PASS: True
```

## What's real vs. what's stubbed

**Real and independently proven (this pass, beyond unit tests):**
- World state, ledger, checkpointing, pause/resume, spend governor (dry
  run above).
- The real `world/main.py` HTTP server boots and serves the full 250-agent
  genesis population with a balanced ledger (fix #3 above).
- $1000 invariant is structurally unbreakable (every `move()` checks it).

**Stubbed / deliberately deferred, called out by the tracks that own them
(not new findings, just consolidated here):**
- `world/app.py` - intentional health-only scaffold, superseded by
  `world/main.py`, kept only for its own test.
- Charter/governance state is not checkpointed (genesis-runner's own
  docstring: "a resume re-seeds a fresh genesis charter rather than
  restoring exact governance state"). Elections beyond the first are not
  auto-advanced.
- `infra/firecracker/`'s real-hardware path is implemented but untested on
  real KVM (no such hardware on this Mac) - the fake-backend path (32
  tests) is the real coverage today.
- Full election phase-machine, freeform-building bill-of-materials
  automation, and most of DESIGN.md section 10's research instruments
  (dream probes, ablation arms) were outside these eight tracks' scope and
  are not evaluated here.

## Ranked risks

**1. RESOLVED 2026-08-17, no longer blocks spend. Broker's actual
model/region topology contradicts DESIGN.md section 4's final decision.**
DESIGN.md section 4 was updated 2026-07-31 to six lanes at 600 RPM
aggregate, matching `broker/config.py` exactly; sections 9 and 11 were
brought into line 2026-08-17. Nothing in the code changed, because the code
was right. Original text follows as the record. `broker/config.py` hardcodes
`MODEL_ALLOWLIST = (kimi-k2.5, deepseek.v3.2)` across
`BEDROCK_LANE_REGIONS = (ap-south-1, us-east-1, us-west-2)` - 6 lanes, 600
RPM aggregate, explicitly "NOT env-configurable... a law, not a deployment
knob" per that file's own comment, built against this orchestration task's
"MEASURED FACTS" brief. DESIGN.md section 4, dated the same day and headed
"final decision 2026-07-30", says the opposite: "ONLY moonshotai.kimi-k2.5
via Bedrock ap-south-1... Single-model: broker allowlist has exactly one
entry; 100 RPM total." DESIGN.md's own sections 9 and 11 were not updated
to match section 4 either (section 9 still says "100 RPM per model (200
combined)"; section 11 still lists DeepSeek pricing as live). So this is
not just broker/ vs. DESIGN.md - DESIGN.md is self-contradictory between
section 4 and sections 9/11. I did not pick a side and silently rewrite
broker's allowlist: it is a real-money architecture decision (which model
gets studied, how fast the budget burns, whether DeepSeek data exists in
this run at all), not a shape mismatch I can fix on inference. **Someone
with authority over DESIGN.md must confirm which is correct before a live
run touches real Bedrock**, then either update `broker/config.py`'s
`MODEL_ALLOWLIST`/`BEDROCK_LANE_REGIONS` to match section 4, or update
DESIGN.md sections 4/9/11 to match the implementation. Both the broker's
own `SpendGovernor` and `run_genesis.py`'s cap default to $400 either way
(consistent), so this risk is about model/region choice and research
validity, not blowing the hard budget cap.

**2. (Blocks live console operation, not the sim itself) The
President/God console cannot currently reach a live world server.**
`game/console/world_client.py` calls an `/admin/*`-prefixed contract
(`/admin/president/petitions`, `/admin/god/blessings`,
`/admin/spend/status`, `/admin/control/pause`, `/admin/alerts`, etc. - 20+
routes) that neither `world/main.py` (which implements
`/president/land-petitions`, `/god/prayers`, etc., no `/admin` prefix, no
spend/control/alerts routes at all) nor `run_genesis.py`'s own embedded
world app (which only implements `/health`, `/tools/registry`,
`/agents/{id}/tool_calls`, `/ledger/*` - genesis-runner's own docstring:
"implements exactly those contracts... nothing more") actually serves.
Confirmed by diffing every route each side calls/exposes: **zero exact
path overlap**. The console's own test suite (179 tests, all passing) only
ever exercises `game/console/tests/fake_world_admin.py`, a self-contained
fake - it has never been integration-tested against either real server.
I did not attempt to hand-write ~20 routes with real request/response
schemas under today's time pressure; that would be exactly the "stub
something and claim it's done" this task explicitly forbids. **The 250-
agent genesis run itself does not depend on this** (run_genesis.py is
fully self-contained), but a human cannot approve a land petition,
grant/silent-deny a prayer, or veto an amendment through the console
against a live run until one side of this contract is rebuilt to match
the other. Recommend: pick whichever side has more real behavior behind
it (world/main.py's routes are wired to real `WorldServer` methods; the
console's schemas are richer) and port the other to match - a half-day to
a day of focused work, not a quick patch.

**3. (Low severity, already fixed) Checkpoint schema drift.** Fix #2 above
is exactly the failure mode to watch for again: `WorldState`'s owned-field
list and any checkpoint adapter that hand-lists those fields are two
sources of truth that can silently drift. Worth a follow-up: either make
`WorldStateCheckpointAdapter` iterate `OWNED_STATE_FIELDS` directly instead
of maintaining a parallel `WORLD_STATE_DICT_MODEL_TYPES`/
`WORLD_STATE_LIST_MODEL_TYPES` pair, or add a test that asserts the two
lists match `state.py`'s `OWNED_STATE_FIELDS` by name so a future new
field fails loudly instead of silently.

**4. (Low severity) Charter/governance state does not survive a resume**
(genesis-runner's own documented, deliberate scope boundary, not something
I changed). A spot-interruption mid-election during a live run would
re-seed a fresh genesis charter on restart rather than resuming the
in-flight election. Acceptable for a first run given DESIGN.md's own
election cadence (every 3 game-days) makes this a narrow window, but worth
fixing before a long unattended run.

## Local bring-up (verified commands)

```bash
cd /Users/septemberai/Vat/kasukabe/app

# full test suite
docker compose build test && docker compose run --rm test

# firecracker track's own suite
cd infra/firecracker && docker compose build test && docker compose run --rm test && cd ../..

# real world server (now the actual composition, not the health stub - fix #3)
docker compose up world
curl http://127.0.0.1:8100/health
curl http://127.0.0.1:8100/state/summary   # {"population": 250, "ledger": {"halted": false, "usd_total_cents": 100000, ...}}
docker compose down

# 250-agent mock dry run (2 game-days, matches this file's "Dry run results")
docker compose run --rm test python3 -c "
import asyncio
from pathlib import Path
from world.run_genesis import GenesisRunner, GenesisRunnerConfig
async def main():
    config = GenesisRunnerConfig(mock_mode=True, checkpoint_dir=Path('/tmp/ckpt'),
                                  budget_cap_cents=40_000, retrieval_depth_fraction=1.0,
                                  world_mode='external')
    runner = GenesisRunner(config)
    await runner.boot(resume=False)
    await runner.start_agents()
    results = await runner.run_burst(288)
    print('ticks run:', len(results), 'ledger halted:', runner.ledger.halted)
    await runner.shutdown()
asyncio.run(main())
"

# broker / game / console dev servers (unchanged by this pass)
uv run uvicorn broker.app:app --reload --port 8200      # or: docker compose up broker
uv run uvicorn game.server:app --reload --port 8400
uv run uvicorn game.console.app:app --reload --port 8300
```

## GO / NO-GO for a live 250-agent genesis run against real Bedrock

**GO** - the simulation mechanics that must hold for six-plus hours
unattended (ledger invariant, checkpointing, crash/resume, spend-cap
enforcement) are real, tested, and independently proven against the full
250-agent population in this pass, not just claimed.

**Risk #1 resolved 2026-08-17, no longer a NO-GO.** DESIGN.md section 4 had
already been updated to 6-lane Kimi+DeepSeek/3-region on 2026-07-31, one day
after this file was written, and sections 9 and 11 were corrected to match on
2026-08-17. `broker/config.py` was never wrong and was not changed. Round 1
ran on 2026-08-01 across all six lanes; see `docs/archive/kasukabe-round-1.html` for what
it spent and `ROADMAP.md` for what it means.

Risk #2 (console) does not block starting the run itself, only live
human-in-the-loop steering of it (petitions, prayers, disasters, veto) -
DESIGN.md section 8 treats that steering as load-bearing for the political
simulation, so a run started without it should be treated as "sim running
headless, unattended by the President/God console" until that gap closes,
not as fully matching DESIGN.md's intended operating mode.
