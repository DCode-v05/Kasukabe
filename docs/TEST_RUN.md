<!-- ARCHIVED 2026-09-05 (EVAL-26). Figures are from runs 1-4, Aug 2026. -->

> **History, not current behaviour.** Several numbers below - notably
> "98% of turns sent with no tools" - were measured before the tool-surface
> and prompt work of runs 19-24 and are contradicted by later runs without
> this document ever saying so. Read it for what the town used to do.

# TEST-RUN — findings across the Kasukabe real-sim runs

This file is the **hand-written** record: what was tried, what broke, and what
was learned. It is not regenerated and nothing overwrites it.

**Each run gets its own live document.** A fresh start of the real sim claims
the next number and keeps it, so run 3 can be compared against run 2 instead
of overwriting it:

- `docs/runs/run-1-report.md`, `run-2-report.md`, … — one per fresh start
- Written by `uv run python -m world.run_report --watch` (every **5 minutes**),
  with one final write the moment the spend cap is reached — so the document
  records how the run actually ended rather than freezing minutes short of it
- **Kept, never deleted.** `docs/runs/` is tracked in git: the engines that
  produced a report are gone the moment the run stops, and the report is the
  only thing left of them
- Every timestamp is **IST, 12-hour, with the date** (`26 Aug 2026, 11:00:46 PM IST`)
- The run's own document is found again by the `<!-- run-started: … -->`
  marker on its first line, so a reporter restarted mid-run refreshes that
  run's document rather than opening a new one.

Each run document holds:

| Section | What it answers |
|---|---|
| The run | residents, tick, calls, dispatch success, spend |
| What the town invented | every `skillify` skill and `toolify` tool, with author and tick |
| Learning by doing | per-agent proficiency and completions |
| The conversation | agent-to-agent speech, grouped into exchanges, tick **and** wall clock |
| What the town could not do | every refused intention, grouped by what was missing |
| The town itself | health, buildings, food, treasury |
| Every resident | all 30 in a table — age, occupation, location, money, skills |
| API calls, minute by minute | every Bedrock call with timestamp, tokens, cost, latency, result |
| **Sessions, per engine** | **per-engine transcripts: input → model (reasoning + output) → tool calls** |
| The instrument | session logs, `SEPTEMBER_HOME`, observatory, engines, authoring tools |
| Preflight at last check | all 18 checks as they last stood |

## The prompt an agent actually receives

For 933 ticks, this was the whole of it:

```
It is game-day 0, 08:00 (day).
With you: a0031, a0087.
Nothing notable happened recently.
```

An agent was never told its own trade, where its work was, or whether it had
eaten — and the people beside it were **agent ids, not names**. A model cannot
greet `a0031`. That prompt is what produced nine utterances in 933 ticks and
residents standing in their houses all day.

It now reads:

```
It is game-day 0, 06:20 (day).
You are Ulrike Greenhow, the town's miner.
You are at home:h0146. Your work is at Kasukabe Ore Outcrop.
You have no food. Upkeep is charged to your household every day whether you
eat or not, and a household that cannot pay goes hungry, then ill, then dormant.
You have 5000 sim in hand.
With you: Halcyon Quarshie (a0031), Milo Quarshie (a0087).
```

Both halves of a name are kept — `say` needs `listener_agent_id`, and the
agent needs something it can address. Every line is a direct read of world
state: no encouragement, no instruction to be sociable, nothing the town has
not actually said. An occupation with no known workplace gets no workplace
line rather than a guessed one.

**Measured, same cast, first 50 ticks:** 14 utterances by tick 42, agents
naming each other and asking about work and trade — against 9 in 933 ticks
before.

## Food, and what is actually wired

`world/life/upkeep.py` implements DESIGN.md section 3 in full — daily
household upkeep, then mild hunger, then a malnutrition ailment, then
dormancy, then death, all reusing the existing health machinery. Its own
docstring says the tick engine should keep `HungerState`.

**Nothing calls it.** `world/family_api.py` reads
`compute_household_bill` for display and no tick applies it. Food is
therefore visible and not yet lethal: an agent is told what it holds and what
upkeep means, and the consequence is not applied. Wiring the daily upkeep
tick is the outstanding piece.

## Per-engine memory: three honest numbers that disagree

The same code, measured three times on three platforms:

| Reading | Figure | How |
|---|---|---|
| Linux, at boot | ~53 MB each | 30 engines, 1.58 GiB, `docker stats` |
| Linux, under load | **~550 MB each** | 10 engines, 5.19 GiB, `/proc` VmRSS; largest 655 MB |
| macOS, under load | **39 MB each** | 30 engines, 1.2 GB, `ps` RSS at game-day 4; median 37, largest 76 |

All three are correct. They differ because of page sharing, not because the
engine differs: on Linux each engine pays for its own copy of the interpreter
and its imports, and macOS shares those pages between thirty children of one
parent.

A single constant is therefore wrong on two platforms out of three. Budget on
550 and preflight refuses a thirty-engine run that macOS handles in 1.2 GB
(observed: `affordable=1`); budget on 39 and a Linux box is exhausted by hour
three. `memory_budget.measured_mb_per_engine()` reads what the engines on this
machine actually cost and preflight prefers it, falling back to the constant
only when there is nothing running — the ordinary pre-boot case, where erring
high is the safe direction.

## Keeping prompts small for a whole run

`world/signal_groundskeeper.py` sweeps every agent's `learning_signals` on a
five-minute timer, deleting only `system_event` rows — the type every reader
in the learning centre already excludes and the one the unfixable
`task_context` path includes. It keeps `tool_call`, `tool_result`, `outcome`,
`event`, `episodes`, `knowledge_store`, `social_graph_*` and
`working_memory_log` untouched.

The defect itself is now **fixed** — DCode-v05 patched
`learning_centre/pipeline.py` under an authorised Law 1 exception, capping the
signal count and excluding `system_event`, and reproduced 14.6 MB → 46.3 KB.
The groundskeeper stays as a second line of defence: it clears any backlog
already on disk from a run that predates the fix, and it is measured rather
than assumed. It stops the
poison queue accumulating for long enough that a five-hour run ends the way it
started. First sweep of the current run: 1,197 rows from 5 of 30 brains.

## THE defect: 98% of agent turns are sent with no tools

Bram Holloway, in his own words, at game-day 2:

> I've now tried every category: movement, production, communication,
> information, assistance, content, estate planning, civic engagement.
> Nothing produces visible results. Nothing changes my location or state.
> ... All execute without visible feedback. My location never changes. My sim
> stays at 3195. No one ever appears. No events ever...

He was right, and he was not confused. His turn shows `0 tool call(s)` and
his text contains `<function_calls> <invoke name="world_transfer">` — he
typed the invocation as prose because **there were no tools in his request to
call**. The name is even correct: the real tools are `world_move`,
`world_harvest`, `world_transfer`.

Measured over one three-hour run:

| | |
|---|---|
| Agent turns (requests carrying a perception) | 6,110 |
| **With tools declared** | **115 — 1.9%** |
| Without tools | 5,995 — 98.1% |

Every symptom chased for two days is downstream of this one number:

- **No conversation** — `say` was not callable, so remarks became text
  nobody heard
- **No work** — `mine`, `harvest`, `fell_timber` were not callable
- **No authoring** — `skillify`/`toolify` were not callable, which is why the
  authoring hint fired 61 times and produced almost nothing
- **149 tool calls out of 8,458 thinking calls** — only the 1.9% could act
- **`0 turn(s) · 0 tool call(s) · exit cancelled`** — the same cause

### Where it is NOT

- Not the registry. Instrumenting `pinned_boot.send_tick` shows
  `registry=57 defs=57` on every tick, for every one of the ten engines.
  The tools are present and correct at the moment the turn begins.
- Not `defer_loading`. World tools never set it; it defaults False.
- Not prompt size. Turns WITH tools are *larger* (median 8,980 input tokens)
  than turns without (3,201) — the schemas are ~5,800 tokens of that.
- Not per-agent. The loss is spread evenly across all ten.
- Not the version handshake. The world reports registry version 49 and the
  engine starts at 0, so the refresh does run.

### Where it is

Between `tools_schema = self.tools.to_tool_defs()` in
`engine_core/thread.py` and the `adapter.stream_with_tools(...)` call about
five hundred lines below it. The registry is full going in and the request
comes out empty.

It **decays with the age of a conversation**, which is the strongest clue:

| calls into the run | turns with tools |
|---|---|
| 0–300 | 55% |
| 300–600 | 18% |
| a three-hour run, overall | 1.9% |

and the turns that lose them are late in a conversation
(`user, assistant, user, assistant, user`), not early ones.

`engine/src/engine_core/` is frozen under ROADMAP Law 1, so this is written
up rather than patched. It is the single highest-value defect in the system:
the town is not unproductive, it is disarmed.

**To reproduce:** `GET /world/engines?verbose=1` shows every engine's recent
stdout including the per-tick `tick tools:` line, and the query at the top of
this section counts tools on agent turns straight out of the trace store.

## Where $40 went — run 13, ten residents, 2h53m

"The agents did nothing and the money is gone" is a fair question and the
answer is specific.

| | |
|---|---|
| Calls | 14,666 — **8,458 succeeded**, 6,208 failed |
| Spend | **$40.00**, entirely on the successful calls (failures are free) |
| Tokens | 103,402,723 in · 1,563,208 out |
| Reached | tick 1,687 (game day 11) |
| Produced | **149 tool calls**, 7 utterances, 1 skill, 1 tool |

**8,458 thinking calls produced 149 actions** — 57 calls per action.

### Four agents burned their whole allowance in the first twenty minutes

| Agent | Calls | Spent | Avg input tokens | Refused, no money |
|---|--:|--:|--:|--:|
| n01 Bram | 1,000 | **$6.00** | 15,909 | 1,277 |
| n03 Tobias | 914 | **$6.00** | 17,346 | 1,280 |
| n05 Nell | 866 | **$6.00** | 18,483 | 1,207 |
| n10 Liora | 463 | **$6.00** | 34,652 | 1,152 |
| n09 Corwin | 1,357 | $4.66 | 8,668 | 2 |
| n02 Ainsley | 1,432 | $4.12 | 7,056 | 3 |
| n08 Idris | 1,271 | $3.33 | 6,442 | — |
| n06 Wren | 865 | $2.89 | 8,124 | — |
| n04 Perrin | 208 | $0.57 | 6,346 | — |
| n07 Marta | 82 | $0.45 | 14,489 | — |

`InsufficientFundsError` fired **4,921 times**: those four spent most of the
run unable to think at all. `SpendCapExceededError` a further 557 once the
broker cap tripped.

### The spend is a handful of enormous calls

```
median call        353 tokens
mean call       12,225 tokens        (35x the median)
largest call   255,735 tokens
```

| | |
|---|---|
| top 1% of calls (84) | $3.85 — 9.6% of budget |
| **top 10% (845)** | **$20.85 — 52% of budget** |
| top 25% (2,114) | $35.77 — 89% |

And front-loaded, which is the actual defect:

| calls into the run | mean input tokens |
|---|--:|
| 0–2,000 | 16,177 |
| 2,000–4,000 | **23,497** |
| 4,000–6,000 | 9,230 |
| 8,000–10,000 | 1,551 |
| 12,000+ | 1,481 |

`signal_groundskeeper` sweeps every 300 seconds, so its first sweep lands
after the budget is already spent. The bloat is fixed eventually and the money
is gone by then.

### One thing that did work

`skillify` and `toolify` were used for the first time in this project: Marta
Thorne wrote the skill `mill_wheat` and invented the tool
`harvest_wheat_for_milling` at tick 40. Naming the verbs in the authoring hint
is what changed. She then made 82 calls on $0.45 and was among those starved
out.

### Two changes that would alter the economics

Both target the measured concentration rather than guessing:

1. **Sweep on boot and every 60s.** The first sweep currently lands after the
   damage; the first 4,000 calls average 16k–23k tokens and the last 4,000
   average 1.5k.
2. **Cap input tokens per call at the broker.** A 255,735-token request is
   never a good call — it is a replay bug that reached Bedrock.

Neither is done. Both are recorded rather than applied, because they change
what a run costs and that is the operator's decision.

## Sessions, per engine

Every engine writes one JSON line per tick it thinks, into its own
`SEPTEMBER_HOME` at `/tmp/kasukabe-agents/<agent_id>/session_log.jsonl`:

```json
{"agent_id": "a0040", "recorded_at": 1787762607.13,
 "input": "It is game-day 0, 08:00 (day). Nobody else is here.",
 "reasoning": "",
 "actions": [{"tool": "world:harvest", "params": {"crop": "potato"}, "result": "..."}],
 "done_status": {"model_id": "moonshotai.kimi-k2.5", "num_turns": 6,
                 "tool_calls_count": 5, "exit_reason": "completed"}}
```

The trace store knows what every call **cost**; only this says what the agent
was **thinking** when it made it. Rendered per engine, it reads as a story:

> **input** — It is game-day 0, 08:00 (day). Nobody else is here.
> **action** `world:harvest` {"crop": "potato", "quantity": 2}
> ↳ Error: harvest requires location 'farmland:potato', actor is at 'home:h0066'
> **action** `world:look_around` → …
> **action** `world:move` {"destination_location_id": "farmland:potato"}
> **action** `world:harvest` {"crop": "potato", "quantity": 2}
> ↳ {"item": "potato", "quantity": 2}

Two checks guard this, and the first is the one that matters:

- **"Session logging is ready for every engine"** runs *before a single engine
  starts*. It does not guess — it creates each engine's `SEPTEMBER_HOME` and
  writes a byte to the exact path that engine will append to, then takes it
  back out. A permission, a full disk or a read-only mount all fail here,
  before any money is spent. A checklist that only finds the problem once the
  run is under way is not a pre-flight check.
- **"Every engine is writing a session log"** runs once the engines are up and
  proves they actually are — a run whose engines are not writing these
produces numbers nobody can interpret afterwards, and the gap is invisible
while it is happening because every other panel looks normal.

## Run 2 — 30 residents (the one that was asked for)

| | 8 agents | **30 agents** |
|---|---|---|
| Backend | `process` | `process` |
| Engines alive | 8/8 | **30/30, 0 boot failures** |
| `SEPTEMBER_HOME`s | 8 | **30** |
| Ticks | 71 | 106 |
| Bedrock calls | 261 | **742** |
| Spend | $1.11 | **$2.48** |
| Dispatch success | 91% | **89.1%** (81 failures, all `bedrock` throttling) |
| Free memory, steady | — | **~1,490 MB, flat for 4 minutes** |
| Agents earning proficiency | 3 | **4** |
| Refusals recorded | 0 | 2 |
| Skills / tools authored | 0 | 0 |

Directed agent-to-agent speech appeared at this size — not just talking to the
room:

```
t90  Cole Pike  to Amos Penn   Kasukabe Government Complex
     "Hendrik Petrov, just come from the cabbage strip but finding the roads…
      unclear. You wouldn't know where a crafter can find a workshop around
      here, would you? Or who's buying produce these days?"

t91  Zane Yates  aloud  The Willows Market and Pharmacy
     "You know who buys raw iron in this town? I have weight to sell and
      daylight burning."

t91  Hana Owens  aloud  home:h0037
     "Dad, Mom—asked proper like, can I head out to the street? Chores are
      done, said I'd do it."
```

### Two corrections this run forced

**1. The per-engine memory figure was more than 2× too pessimistic.**
`MB_PER_ENGINE_PROCESS` was 110 MB — an import measurement plus a guess. Six
live engines measured by `ps` came to **48 MB each**. The constant is now 60
(measured, plus headroom for the tick-time peak), and the preflight stopped
refusing runs that fit comfortably. 30 agents held steady at ~1,490 MB free
for the whole run.

**2. The namespace backend cannot host engines in the app's interpreter.**
Not a dependency problem — an interpreter one:

| | Python | `enable_load_extension` |
|---|---|---|
| app venv | 3.12.5 | **absent** |
| engine venv | 3.11.14 | present |

The engine's vector memory needs the `vec0` SQLite extension, and the app's
Python was built without extension support at all. `aiosqlite`, `septemberai`
and `sqlite-vec` were each installed in turn and each got one step further
before this wall. They have been removed again; the app venv is back to its
own dependencies.

So the namespace backend still needs what was already identified: **one host
subprocess running the engine's own venv**, hosting all N engines behind a
single socket. Its measured 2.8 MB/engine stands; only the hosting does not.

**30 agents did not need it.** At 48 MB each they fit the process backend with
room to spare, which is why this run used it.

---

## Run 3 — the instrument describes the run, not the file

Four things were wrong, and they shared one cause: **the viewer read a
build-time cast, not the running world.**

`agents.js` bakes all 314 residents in at build time. A 30-agent run therefore
drew 314 people on the map, listed 314 under People, and averaged money across
284 residents who were not in the simulation. Every figure in the instrument
was about a cast that did not exist.

`live-cast.js` intersects the two — the world says WHO is real, the static cast
says WHAT THEY ARE LIKE — and prunes `AGENTS` in place, so the map and every
book are correct without each panel learning about it. In place because
`AGENTS` is a top-level `const`: it can be spliced, not reassigned, and every
module holds the same reference.

| | Before | After |
|---|---|---|
| Map + books cast | 314 | **30** |
| Live badge | "28 of **314** on Bedrock" | "28 of **30** on Bedrock" |

With no world bound nothing is pruned — the full 314 is the honest answer when
there is no run to be narrower than.

### Talk is now exchanges, not lines

A flat feed cannot answer "who spoke to who, and what came back". Speech is
grouped into threads by place and participants with a tick gap — **not** by
`conversation_id`, because `say` mints a fresh id per utterance, so every reply
would be its own thread of one. A thread shows every turn and says plainly
whether anyone answered (`3 turns` / `no reply yet`).

### A new book: Authored

The Workshop says what the town has. **Authored** says *which residents are
authoring* — the split between authors and everyone else, which is the finding
in a run where most agents never invent anything. Grouped by author, showing
the verb they used (`skillify` / `toolify`), what they made, its steps, their
proficiency and how many times they have completed it.

Live from the run:

```
2 authors · 2 skills written · 0 tools invented

Lior Jarrah   a0064
  skillify   Morning Pharmacy Opening    4 steps · prof 1  · 0 completions
Halcyon Quarshie  a0031
  skillify   observe_and_record          3 steps · prof 18 · 2 completions
```

### A name bug this surfaced

The Authored page first showed `a0064` as **"Hector Stone"** while the Workshop
called the same agent **"Lior Jarrah"**. `agents.js` is a *different draw* of
the town and assigns different names to the same ids. One agent, two names, no
way for a reader to know which was real.

Fixed at the source: `/world/utterances` and `/world/authored` now resolve
names from the **running genesis**, and the viewer prefers those over the
static cast. Talk, Authored and Workshop now agree.

### Ready for a 3–6 hour run

Two things would have ended a long run, both found by measurement:

| | |
|---|---|
| `tool_invocations` | appended on **every successful call by every agent** — uncapped. Now a 5,000-entry ring |
| `tool_refusals` | same shape — now a 2,000-entry ring |
| `utterances` | already capped at 2,000 |

The archive is not lost: invocations are mirrored into the trace store and
refusals are grouped by the observatory as they arrive. These lists are the
live tail the running world needs, not the history.

**The trace database is the remaining watch item.** It grew to **256 MB across
4,305 calls — about 61 KB per call**, most of it full request transcripts. At
the observed rate a 6-hour run is roughly **18,000 calls ≈ 1.1 GB**. That fits,
but it is worth archiving `data/kasukabe.db` between runs rather than letting
it accumulate.

Spend, measured: **~$1.10 per 10 minutes with 30 agents**, so 6 hours is on the
order of **$40**. `SPEND_CAP_USD` stops the run before the wall clock does.

**Tests:** `test_long_run_caps.py` — 4.

---

## Result in one table

| | |
|---|---|
| Agents | **8**, all alive at stop (`{"alive":8,"expected":8,"dormant":0}`) |
| Backend | `process` — one OS process per engine |
| Ticks reached | 71 |
| Bedrock calls | **261** |
| Spend | **$1.11** of a $3.00 cap |
| Dispatch success | **237/261 = 91%** (24 failures, all tagged `bedrock`) |
| A2A utterances | 4 |
| Skills/tools authored by agents | **0** |
| Agents earning proficiency by working | **3** — forestry 13, mining 18, farming 22 |
| Tool refusals | 0 |

---

## Docker: not removed, and not used

**Nothing was stripped, because nothing needed stripping.** The three things
believed to be in the way were already absent from the path:

| Believed | Measured |
|---|---|
| Docker runs the engines | `run_genesis.py` never invokes Docker. It spawns OS processes |
| The sandbox blocks the run | `engine/src/sim/` imports only `sandbox.permissions`, a permission *data type*. A sim engine registers world tools, a session log and a memory stream — **no shell tool** — so the bubblewrap executor is never reached |
| Each engine needs its own home | Already `<home_root>/<agent_id>`, holding that engine's SQLite, skills, and everything else |

Docker remains in the repo, untouched. It is simply not on the path a run
takes. Deleting `engine/src/sandbox/` would also break ROADMAP Law 1 —
changes under `engine/src/` outside `sim/` are *"a finding written up rather
than patched around"*.

## Namespace backend: built, and NOT used for this run

A `namespace` backend was built (`app/world/namespace_supervisor.py`): one
process, one `SEPTEMBER_HOME` per agent, no container and no port. Measured
with 30 real engines booted:

| Backend | Per engine | 30 agents | 50 agents |
|---|---|---|---|
| `process` | ~110 MB | 3.3 GB | 5.5 GB |
| `namespace` | **2.8 MB** | 190 MB | 270 MB |

**It was not used for this run, because it does not work yet.** Attempting 50
agents on it failed all 50 boots, correctly recorded as failed units rather
than crashing the run:

```
WARNING namespace a0076 failed to boot: ModuleNotFoundError: No module named 'src'
```

Two causes, found in order:

1. **`src` was not importable.** `ProcessSupervisor` spawned each engine with
   `cwd=engine_repo`, so Python found the engine package by itself. Sharing
   one process means sharing one `sys.path`. Fixed — the supervisor now puts
   the engine repo on the path.
2. **The venvs are not the same interpreter.** The app runs **Python 3.12**
   with its own dependency tree; the engine runs **Python 3.11** with another.
   `aiosqlite` was missing, then `september`. Chasing packages one at a time
   into the app venv is the wrong fix: it merges two dependency trees by hand
   across two interpreter versions.

**What the namespace backend actually needs:** one host subprocess running the
*engine's own* venv, hosting all N engines behind a single socket. That keeps
the memory win (one engine process, not fifty) and respects the interpreter
boundary. It is not built.

So: **50 agents did not run.** 8 did, on `process`, which is what fits 8 GB
today. A memory preflight (`app/world/memory_budget.py`) now refuses an
oversized run before the first spawn rather than letting the machine swap.

---

## What the agents actually did

Real speech, from real Bedrock calls:

```
t36  Cole Pike    aloud  Harvey Oak Supply Store
     "Good morning. I've got three sound timbers here, fresh from the forest.
      You buying wood today, or should I be looking elsewhere?"

t37  Delia Coop   aloud  Kasukabe Government Complex
     "Pardon—Ulrike Greenhow, resident miner. Heard of any work needing stone
      this morning? Or if someone's looking for assay?"

t38  Delia Coop   aloud  The Willows Market and Pharmacy
     "Hey - anyone know how you reach Harvey Oak from here? Or if there's a
      smelter buying copper? I've got ore to move and no map that makes sense."
```

**Part A is working in a live run.** Three agents earned proficiency purely by
doing the work — forestry 13, mining 18, farming 22 — which is the practice
curve operating on real Bedrock decisions, not a test fixture.

## skillify / toolify: wired, reachable, unused

All six authoring tools are served to agents (verified against the live
registry): `skillify`, `toolify`, `describe_primitives`, `quote_action_cost`,
`revise_skill`, `revise_tool`.

**No agent used them, and the reason is measurable, not mysterious.** The
authoring hint fires only after an agent repeats one tool **8 times** with
nothing written down. At stop, the busiest agents had **1, 2 and 3**
repetitions. Nobody crossed the threshold, so nobody was ever told they could
author anything — which is the evidence-driven design working as specified,
on a run that was simply too short.

To see authoring, the run needs to reach ~8 repetitions of a single tool per
agent. At 71 ticks with 3 repetitions, that is roughly **3–4× longer**, or a
lowered `AUTHORING_HINT_REPEATS`.

---

## New surfaces built for this run

| Surface | Where |
|---|---|
| **Talk book** — A2A speech, live, newest first | `app/game/static/js/township/talk.js` |
| `GET /world/utterances` | `app/world/run_genesis.py`, `app/world/main.py` |
| `GET /world/authored` — what the town invented, per agent | same |
| `/api/town/talk`, `/api/town/authored` | `app/game/server.py` |
| **Authored strip** on the resident file — tools invented, workflows written, proficiency earned | `talk.js` + `css/parts/revamp.css` |

`say` previously emitted an event only the trace store consumed, so two
residents could hold a conversation and the viewer had no way to show it.

**One honest limitation:** the world server cannot name a speaker.
`AgentLifeRecord` has no name field — names live in genesis, not world state —
so `/world/utterances` returns agent ids and the viewer resolves them from its
own roster. An agent that addresses its own id is rendered "aloud" rather than
"to themselves", because that is a model naming the wrong listener, not a
rendering bug.

---

## How to reproduce

```bash
cd app

# 1. broker — real Bedrock. Set SPEND_CAP_USD in .env first.
uv run uvicorn broker.app:app --port 8200 &

# 2. a cast that fits the machine
uv run python -m world.make_genesis_subset 8

# 3. the town — 8 engines, OS processes, no Docker
KASUKABE_HOME_ROOT=/tmp/kasukabe-agents \
uv run python -m world.run_genesis \
  --genesis-path world/genesis_8.json \
  --world-mode embedded --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --supervisor-backend process \
  --engine-repo ../engine --no-resume --tick-interval-s 15 &

# 4. the viewer
WORLD_BASE_URL=http://127.0.0.1:8100 \
  uv run uvicorn game.server:app --port 8400 &
```

Then open **http://127.0.0.1:8400/township-v2.html** → 📚 Town books → **Talk**.

Watch it live:

```bash
curl -s localhost:8100/world/utterances | python3 -m json.tool
curl -s localhost:8100/world/authored   | python3 -m json.tool
sqlite3 data/kasukabe.db \
  "select count(*), round(sum(cost_nano_dollars)/1e9,4) from llm_calls;"
```

---

## Run 4 — the six-hour run, and why the five-hour run has not started

A 30-resident run reached tick 933 (game day 6) on real Bedrock before it was
stopped. It is the longest run this project has done and it is the reason the
five-hour run is not running yet: it surfaced four defects, two of which would
have taken the machine down rather than merely spoiling the result.

| | |
|---|---|
| Residents | 30, all engine-backed, all on the map |
| Tick reached | 933 (game day 6) |
| Bedrock calls | 9,848 |
| Dispatch success | 90.9% (893 failures) |
| Spend | $22.14 of a $25 cap |
| Authored tools / skills | **none** |
| Utterances | 9 |

### Finding 1 — the trace store was about to fill the disk

`data/kasukabe.db` reached **5.9 GB** on a laptop with **1.2 GB free**. The
whole of it is `llm_calls` (6,077 MB of 6,079 MB by page count). Request bodies
averaged **630 KB**, and the largest single request was **36.5 MB**.

The disk preflight check passed the entire way down, because its estimate was a
constant — 61 KB per call, taken from a short healthy run. A guessed denominator
is exactly as dangerous as no check at all.

Fixed, in two places:

- `broker/trace.py` now caps a stored body at 64 KB (`TRACE_BODY_LIMIT`,
  `KASUKABE_TRACE_BODY_LIMIT=0` to disable). The truncation is announced in the
  row — `_truncated`, `_original_bytes` — never silent. Measured after the fix:
  largest stored body 69 KB, average 44 KB.
- `world/preflight.py` now *measures* KB-per-call from the tail of the trace
  store instead of assuming it, and reads `_original_bytes` on capped rows so
  the cap cannot hide the thing the cap exists to survive.

### Finding 2 — the prompts are unbounded, and it is the frozen code

The 630 KB average is not the trace store being greedy. It is what is being
*sent*. One request carried **118,179 replayed working-memory records**, of
which **36,673 were bare `thread_state_change`** state-machine noise.

That is 506 of the 893 failures on its own:

| Cause | Count |
|---|---:|
| `request body too large` (Bedrock refuses to buffer it) | 309 |
| `BadRequestError 400` (the same request, refused earlier) | 197 |
| throttled | 150 |
| every lane circuit-open (a consequence of the above) | 114 |
| out of wallet money (working as designed — cognitive poverty) | 97 |
| service unavailable | 23 |

The cause is `engine/src/learning_centre/pipeline.py`, in `run_batch`:

```python
task_context = json.dumps(
    [
        {"type": s["type"], "source": s["source"], "subject": s["subject"],
         "content": s["content"][:300], "temporal_ref": s.get("temporal_ref")}
        for s in task_signals          # <- no cap on how many
    ],                                 # <- and no system_event filter
    default=str,
)
```

Each signal's *content* is trimmed to 300 characters; the *number* of signals
is not bounded at all. Two sibling call sites in the same file — the
`all_signal_context` dump forty lines above, and `build_episode_value` below —
both exclude `system_event`. This one does not. That asymmetry is what makes it
an oversight rather than a design choice.

It is a growth curve, not a constant, which is why a short run looks perfectly
healthy and a long one does not. Measured within a single fresh run, in call
order:

| calls into the run | true average request body |
|---:|---:|
| 0–150 | 48 KB |
| 150–300 | 54 KB |
| 300–450 | 64 KB |
| 450–600 | 65 KB |
| 600–750 | 74 KB |
| 750–900 | 77 KB |
| 900+ | 126 KB |

An earlier two-hour run in the `kasukabe-real` worktree is the same defect at
its end state: **38,034 calls, 552 KB average, 49 MB largest, and 36,798 of
them failed — 96.7%.** It wrote 21 GB of trace in two hours. By that point
essentially every call was being refused for size.

That same run's dispatch success was **99.3%** (6 failures in 904 calls) — a
run twenty minutes old is fine. The six-hour run reached a 630 KB average and
90.9%. Nothing is wrong at the start and everything is wrong by hour six, so
any verification short enough to be convenient will pass.

`engine/src/learning_centre/` is frozen under ROADMAP Law 1, so this is written
up rather than patched. **The one-line shape of a fix**, for whoever owns that
file: exclude `system_event` from `task_signals` the way its two siblings
already do, and cap the list at the last N. `src/memory/wm_formatting.py`
already defines both — `NOISE_EVENTS = {"thread_state_change"}` and a
`max_items` window — and neither is reachable from this code path.

Until it is fixed, `world/preflight.py`'s **"Prompt size is sane"** check
blocks any run whose trace shows an average request body over 64 KB.

### Finding 3 — a run could boot zero engines and say it launched thirty

`GenesisConfig.supervisor_backend` defaulted to `"namespace"`, a backend that
cannot host an engine (documented in Run 3 above: the app venv is Python 3.12.5
and its `sqlite3` has no `enable_load_extension`). Every one of the thirty units
failed with `ModuleNotFoundError: No module named 'aiosqlite'`, and the runner
logged:

```
INFO kasukabe.genesis_runner launched 30 agent units (mock_mode=False)
```

and went on ticking a town of statues. Every panel in the viewer read exactly as
it would for a healthy run.

Fixed: the default is `"process"`, the count is of units that *booted*, and a
launch where nothing booted raises `SystemExit` naming the backend.

### Finding 4 — the process backend could not resume from a checkpoint

`self.genesis` was assigned only inside `seed_genesis()`, which runs only when
there is no checkpoint. `start_agents()` reads it to write each engine its
persona, so resuming any run died at:

```
AttributeError: 'GenesisRunner' object has no attribute 'genesis'
```

before a single engine started. Fixed: `boot()` loads the genesis record on both
paths. The checkpoint carries the world's state; the genesis file carries who
these people are, and no checkpoint replaces it.

## The preflight checklist

Fifteen checks. Thirteen run before a single engine starts; three more run once
the world is up and are served live at `GET /world/preflight`. A failing
**critical** check refuses to start the run (`SystemExit`, with the rendered
list); a **warning** says so and continues.

| # | Check | Blocks? | Exists because |
|---|---|---|---|
| 1 | Genesis cast loads | yes | a typo'd path silently seeded the wrong town |
| 2 | Cast size matches the run | yes | a 314-cast run asked for 30 |
| 3 | USD ledger invariant ($1000) | yes | the invariant is the ledger's only self-check |
| 4 | Broker is up | yes | a run without it fails at the first tick |
| 5 | Broker is on real Bedrock | yes | canned text is indistinguishable downstream |
| 6 | Spend cap is set | yes | a five-hour run without one is unbounded |
| 7 | Cap covers the intended run | no | $25 buys 3.8h of a 5h run |
| 8 | Memory fits the engine count | yes | 8 GB machine, measured 48 MB/engine |
| 9 | Disk has room for the trace store | yes | Finding 1 |
| 10 | Prompt size is sane | yes | Finding 2 |
| 11 | Trace store writable, `failed_at` present | yes | the hop column the failure table needs |
| 12 | `SEPTEMBER_HOME` root is writable | yes | every engine's own sqlite lives there |
| 13 | Engine repo and venv present | yes | the 3.11 venv, not this one |
| 14 | Authoring tools reachable by agents | yes | an agent that cannot call `skillify` makes the run pointless |
| 15 | Every engine booted | yes | Finding 3 |
| 16 | Engine ports are free | yes | a previous run's engines outlive `pkill`; 23 of 30 died with `[Errno 48]`, surfacing as "exited with code 3" |
| 17 | **Every engine is writing a session log** | yes | the only record of *why* an agent acted; a silent engine is invisible on every other panel |
| 18 | **Broker is writing to this trace store** | yes | delete a trace file under a running broker and it writes to the unlinked inode forever; every other surface looks healthy |
| 19 | World is ticking | no | a world stuck at tick 0 means no engine has acted |

In the browser it is the **Preflight** book, first in the left dock
(`static/js/township/preflight.js`). It polls `/api/town/preflight` every 30s,
collapses to one summary row when the run is ready, and keeps every failing
line open with its fix when it is not. The panel's left edge and
`document.body.dataset.preflight` both carry the state, so a blocked run is
visible without reading a word.

Live, against the running world:

```
[✓] Genesis cast loads            world/genesis_30.json — 30 agents
[✓] Cast size matches the run     genesis has 30, run wants 30
[✓] USD ledger invariant ($1000)  total_cents = 100000
[✓] Broker is up                  http://localhost:8200
[✓] Broker is on real Bedrock     inference = 'bedrock'
[✓] Spend cap is set              SPEND_CAP_USD = 25.00
[!] Cap covers the intended run   $25.00 ≈ 3.8h at the observed rate
[✓] Memory fits the engine count  1800 MB needed, 2130 MB free
[✓] Disk has room for the trace   18893 MB free; a 5h run projects 350 MB
[✓] Prompt size is sane           44 KB average request body
[✓] Trace store writable          data/kasukabe.db
[✓] SEPTEMBER_HOME root writable  /tmp/kasukabe-agents
[✓] Engine repo and venv present  ../engine/.venv/bin/python
[✓] Authoring tools reachable     48 tools served; missing: none
[✗] Every engine booted           29/30 alive
```

## All thirty on the map, and what each is doing

Two things were wrong and both are fixed.

**"29 of 30 on Bedrock."** `game/position.py`'s `township_zone_for()` resolved a
location to a township district through two exact tables. `Kasukabe Grocery` — a
genesis-minted business — was in neither, so it returned `None` and the resident
standing in it was simply not drawn. There was no way to tell which one.

The tables now fall through to a **trade-word sweep** (`grocer` → grocery,
`clinic` → hospital, `pub` → inn), matched against *words* rather than raw
substrings, because `Ferris Hardware Store` contains `ore` and `Public Library`
contains `pub`. `township_zone_detail()` returns the district *and* whether the
town's own vocabulary named it exactly; a guessed placement is drawn and labelled
`approx. position` rather than asserted. A live location that still resolves to
nothing draws at the square — `game/server.py`'s `_drawable_zone`, which the map
loop and the engine-status badge now share so they can no longer disagree.

**A click showed who someone is, never what they are doing.** `current_action`
was synthesised from dormancy and health alone, so a healthy, busy farmer read
as a blank card. `GET /world/activity` now joins each agent's completed
`ToolInvocationRecord`s with their utterances and phrases the newest one:

```
Doing   looking around                                    tick 937
  tick 937  look_around
  tick 937  move          destination_location_id=Johnson Park
  tick 937  list_places   query=cafe
  tick 937  move          destination_location_id=Hobbs Cafe
```

Every tool name and parameter key in the phraser was read off this world's own
`/tools/registry` — a name invented there would read as a real activity and
never once fire. `skillify`/`toolify` rows are accented, because authoring is
the thing the run exists to observe. Agent-authored text lands in `innerHTML`,
so the block is the one place in `township-v2.js` that escapes what it prints.

Verified in headless Chrome against the live run:

```
agentsTotal 30    liveBound 30    zoneCount 30    drawn 30
doingCount  30    recentCount 28  unzoned []      approx ['a0204']
hudAlive    30    hudDead 0       hudWealth 149,496   hudTreasury 504
doingBlock  "Doing looking around tick 937 ..."   actRows 4
preflightState blocked            preflightRows 2
```

## Session logs and per-agent state

`SEPTEMBER_HOME` is `/tmp/kasukabe-agents/<agent_id>` and each engine owns its
directory outright:

```
/tmp/kasukabe-agents/a0015/
  brain.sqlite          the agent's own memory store
  brain.sqlite-wal      3.9 MB after ~900 ticks
  persona.txt           3.7 KB, written by start_agents() before boot
  session_log.jsonl     1.6 KB, appended as the agent acts
```

No Docker, no container, no shared database. Thirty directories, thirty
processes, one `SEPTEMBER_HOME` each.

## Docs that update themselves during a run

`world/run_report.py --watch` now defaults to **90 seconds** (bare `--watch`;
an explicit interval still overrides). It rewrites `docs/RUN_REPORT.md` with the
resident table, the API-call timeline with timestamps, calls-per-minute, spend,
the authored tools and skills, per-agent proficiency, and the full
agent-to-agent conversation grouped into exchanges. A failure to write is logged
and retried rather than fatal — a reporter that dies takes the run's only
written record with it.

## Honest state

- **Works:** 30 residents on real Bedrock, all thirty drawn on the map, a click
  showing what each is doing from its own completed calls, per-agent
  `SEPTEMBER_HOME` with its own sqlite and session log, practice-earned
  proficiency, spend capped and metered, a fifteen-point preflight that refuses
  to start a bad run and shows the reason in the browser.
- **Blocks the five-hour run:** the unbounded working-memory replay in
  `engine/src/learning_centre/pipeline.py` (Finding 2). It is frozen code. Until
  it is fixed, preflight check 10 refuses to start, and a run started anyway
  loses roughly half its calls to `request body too large`.
- **Open:** one engine of thirty (`:9101`) stopped during the verification run
  without a logged reason. `GET /world/engines` now names which units are down
  and why, so the next occurrence is diagnosable rather than a bare `29/30`.
- **Not observed:** any agent using `skillify` or `toolify`, in 933 ticks. They
  are wired, reachable, and priced; no agent repeated one job often enough to be
  prompted. Utterances were also thin — 9 in 933 ticks.
- **`SPEND_CAP_USD` is $25**, which preflight measures at 3.8 hours. Five hours
  needs about $33.
- **The AWS keys in `app/.env` still need rotating.** They were pasted into a
  chat and must be treated as compromised.
