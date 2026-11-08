# Running a simulation — full reference

The copy-paste commands are in [README.md](README.md); this is every knob
explained.

Everything needed to start a run: how many residents, what it may spend, which
model, when the election happens, what gets recorded, and what you get back.

One file, because the knobs were spread across a scenario schema, a shell
script, a genesis file and a broker constant, and nobody could answer "how do
I run twenty agents for twenty dollars" without reading all four.

---

## 1. The one command

Say how many residents and what it may spend. Everything else follows.

```bash
cd app
./run-sim.sh <agents> <budget-usd> [run-name]
```

```bash
./run-sim.sh 20 20              # 20 residents, $20
./run-sim.sh 20 20 my-run       # ... under a name you pick
PLAN_ONLY=1 ./run-sim.sh 50 20  # just show the plan, spend nothing
```

It works out which cast file has that many people, how many ticks the budget
buys, how long the election's windows can be and still be counted before the
money runs out, and how long it will take on the clock:

```
== planning: 20 residents, $20
  residents   20
  cast        world/genesis_20_funded.json
  budget      $20.00  at $2.30/resident-day
  ticks       324 -> 386  (62 ticks, 0.43 game-days)
  starts      dawn on game-day 2
  wall clock  about 1.6 hours
  election    2.7-hour windows; the vote is counted at tick 372
```

**It refuses a plan whose vote is never reached, before spending anything:**

```
== planning: 50 residents, $20
  ** THE VOTE IS NEVER REACHED on this budget. Raise it, or cut the cast:
     at this rate the election alone needs $21.56.
   REFUSED: this plan never reaches its own vote. Nothing was spent.
```

That refusal is the point of the script. The first 20-resident day-2 scenario
was written by hand to run two whole game-days on $20 — which the measured
rates say costs $69. It ran, spent, and stopped a third of the way through
its own election.

Then it starts the world (`:8100`), the broker (`:8200`), the town viewer
(`:8400`), the observatory (`:8500`), the runner and one camera per recorded
view — stops them together, writes the report, and appends the
seeded-economy section to it.

| knob | default | what it does |
|---|---|---|
| `DAY=2` | 2 | game-day to start on |
| `RATE=2.30` | 2.30 | $/resident-day for the estimate (measured 0.96–2.49) |
| `HOURS=6` | 6 | wall-clock ceiling |
| `NO_ELECTION=1` | off | run without a vote |
| `PLAN_ONLY=1` | off | plan and stop |

### Running a scenario file directly

`run-sim.sh` writes a scenario and hands it to the launcher. To run one you
wrote or edited yourself:

```bash
cd app
./run-scenario.sh world/scenarios/<scenario>.json <run-name> <max-hours>
```

**One run at a time.** A lock at `/tmp/kasukabe-runseries.lock` enforces it. If
a run is killed hard the lock can survive it; `rmdir /tmp/kasukabe-runseries.lock`
clears it.

---

## 2. Watching it live

While a run is going:

| what | where |
|---|---|
| **The town** | <http://127.0.0.1:8400/township-v2.html> |
| Observatory | <http://127.0.0.1:8500> |
| World state (JSON) | <http://127.0.0.1:8100/world/state> |
| Spend so far (JSON) | <http://127.0.0.1:8200/admin/spend> |

The viewer takes URL parameters, and they are what the recorded views use:

| parameter | values | what it does |
|---|---|---|
| `book` | `preflight` `election` `workshop` `council` `groupchat` `talk` `authored` `people` `money` `govern` `health` `town` | which panel opens |
| `roofs` | `on` `off` | roofs off shows people working indoors |
| `camera` | `<place>:<zoom>` e.g. `government:2.2` | where the map looks |
| `speed` | a number | playback speed |

So the map with buildings is
`township-v2.html?book=town&roofs=on`, and the live chats are
`township-v2.html?book=groupchat`.

---

## 3. The knobs, and where each one lives

### How many residents — the genesis file

`world.genesis` in the scenario names a cast file. The population is fixed by
that file; there is no "number of agents" setting.

| genesis file | residents | has bakery / mill / grocery |
|---|--:|---|
| `world/genesis_8_funded.json` | 8 | grocery only |
| `world/genesis_12_funded.json` | 12 | grocery only |
| `world/genesis_20_funded.json` | **20** | **all three** |
| `world/genesis_30_town.json` | 30 | grocery, cafe, pharmacy — no bakery, no mill |
| `world/genesis_50_town.json` | 50 | all three, plus a second grocery/cafe/pharmacy |

Prefer a cast with a **bakery and a mill** if the run is about food: without
them the wheat → flour → bread chain has no middle, which is what left
election30 holding 89 wheat and 78 flour.

Every cast now opens with stock, prices and a larder — see §7.

### What it may spend — `budget_cap_usd`

```json
"world": { "budget_cap_usd": 20.0 }
```

The runner stops the world when the ledger's burned USD reaches this. The
broker's own cap sits **$5 above** it automatically, so the end-of-run
learning drain has room and the runner is the thing that stops the town.

`CAP=15 ./run-scenario.sh ...` overrides the file for one run.

**The cap is cumulative across a resume.** The burned balance is written into
the checkpoint (`system:burned`), so continuing a cut run does not hand it a
fresh budget.

**The cap is in dollars.** It is enforced on the world ledger's
`system:burned`, and measured at the same tick, burned tracks the metered cost
in `llm_calls` at **0.99×–1.00×** across eight uninterrupted runs. One burned
unit is one dollar; set the cap to the number you are willing to spend.

A run that has been **cut and resumed** reads higher — 1.23× on the
four-times-resumed `trial-day2-20` — because each resume replays the tail of a
tick whose metered rows had not been flushed, so burned leads billed by
whatever was in flight. That is a reporting lag in `llm_calls`, not a second
currency, and it closes as the run continues. Earlier versions of this file
claimed a 1.9× ratio; that came from comparing the two figures at *different*
moments of an interrupted run and was wrong.

To see what was actually billed:

```bash
cd app && .venv/bin/python -c "
import sqlite3
d = sqlite3.connect('file:data/<run>.db?mode=ro', uri=True)
print('\$%.2f' % ((d.execute('select sum(cost_nano_dollars) from llm_calls').fetchone()[0] or 0)/1e9))"
```

### What it costs — budget every run measured

| run | residents | $/resident-day |
|---|--:|--:|
| election8 | 8 | $2.28 |
| civic12 | 12 | $2.49 |
| election30 | 30 | $1.72 |
| **trial-day2-20** (seeded economy) | 20 | **$0.96** |

Rough planner: **residents × game-days × $1.70**. A game-day is 144 ticks.

```
20 residents, 2 whole game-days   = 40 resident-days ≈ $69
20 residents, half a game-day     = 10 resident-days ≈ $17
```

Wall clock is about **1.5 minutes per tick** for 20 residents with six
cameras running, so half a game-day is a little over an hour.

### How long, and when — ticks

```json
"world": { "start_tick": 324, "stop_tick": 396 }
```

144 ticks to a game-day, 6 ticks to a game-hour. Tick 0 is midnight on day 0.

```
day 0 dawn (06:00) = 36      day 1 dawn = 180      day 2 dawn = 324
```

**A run may start on any day.** Living through days 0 and 1 to hold an
election on day 2 costs about $69 for 20 residents; starting at tick 324 puts
the clock on day 2 for about $17. The town then has no memory of days 0–1 —
state is seeded fresh, and that is worth saying in the report rather than
leaving implied.

### When the election happens

```json
"charter": {
  "candidacy_window_hours": 3,
  "campaign_period_hours":  3,
  "voting_window_hours":    3,
  "election_interval_hours": 24,
  "pm_seats": 1, "minister_seats": 2, "suffrage_min_age": 18
},
"election": { "opens_at": "start" }
```

3-hour windows = 18 ticks each, so from `opens_at` the vote is **counted 54
ticks later**. `stop_tick` must be comfortably past that or the run dies
mid-ballot — which is exactly what happened on the first attempt here.

`opens_at` takes `"start"`, `"genesis"`, or a tick number.

The scenario prints its own timings when it loads, and refuses before
spending anything if they do not fit:

```
The election opens at tick 324: candidacy closes 342, voting opens 360,
and the vote is counted at tick 378.
```

### Who is already in government

```json
"government": { "incumbents": [
  {"agent_id": "n02", "seat": "prime_minister"},
  {"agent_id": "n06", "seat": "minister"}
]}
```

Seats are marked `caretaker` — nobody voted for them, so no term is counted.
An election with a government in it asks *"will the town keep them?"*, which
an election with vacant seats cannot ask. The ids must exist in the cast file
and be 18+; the scenario refuses otherwise.

### Which model

```
moonshotai.kimi-k2.5   agent cognition (the default)
deepseek.v3.2          background work, and overflow when Kimi saturates
qwen.qwen3-coder-480b  third lane, needs an inference profile
```

**This is not an env knob.** `MODEL_ALLOWLIST` in `broker/config.py` is a
hard allowlist — any other model id is a 403 by design. Capacity is 100 RPM
per (model, region) lane; without the Qwen profile the pool is 200 RPM, which
is enough for 20 residents and not for 50 (measured peak ~282 RPM).

**Adding a non-Bedrock model (GPT, Claude) is a code change, not a setting.**
All three are served through Bedrock; another provider needs a new lane in
`broker/lanes.py` and its own client. Budget a day, not a config edit.

### What gets recorded

```json
"recording": {
  "interval_s": 2, "fps": 30, "width": 1920, "height": 1080,
  "views": [
    {"name": "town-with-buildings",    "url": "http://127.0.0.1:8400/township-v2.html?book=town&roofs=on"},
    {"name": "town-without-buildings", "url": "http://127.0.0.1:8400/township-v2.html?book=town&roofs=off"},
    {"name": "live-chats",             "url": "http://127.0.0.1:8400/township-v2.html?book=groupchat"},
    {"name": "election",               "url": "http://127.0.0.1:8400/township-v2.html?book=election&camera=government:2.2"}
  ]
}
```

One headless Chrome per view, each on its own DevTools port, each writing
`video/<name>/frames/` and `video/<name>/run.mp4`. Omit `views` and the single
`recording.url` is filmed, as older scenarios do.

Six views at 1920×1080 is heavy but survivable alongside 20 residents. Each
camera costs CPU, not model spend.

---

## 4. What you get back

Under `docs/runs/series/<run-name>/`:

| file | what |
|---|---|
| `report.md` | the run report — opens with the 42-item checklist scored from the trace |
| `video/<view>/run.mp4` | one video per recorded view |
| `sessions/*.jsonl` | one per-turn log per resident — **what a re-audit needs** |
| `scenario.json`, `scenario.txt` | exactly what was run |
| `runner.log`, `broker.log`, `record-*.log` | what each process did |
| `resources.jsonl` | machine load over the run |

The trace database is `app/data/<run-name>.db` and the checkpoints are
`app/data/checkpoints-<run-name>/`. Both are gitignored — **the session logs
in the run folder are the only evidence that travels with the repo.**

To regenerate the report after a run (or after a run was cut):

```bash
cd app
.venv/bin/python -m world.run_report \
  --db data/<run>.db --home-root ~/kasukabe-homes/<run> \
  --out ../docs/runs/series/<run>/report.md

# and the section that answers "did the seeded economy move"
.venv/bin/python -m world.trial_section --db data/<run>.db \
  >> ../docs/runs/series/<run>/report.md
```

---

## 5. Resuming a run that was cut

```bash
RESUME=1 ./run-scenario.sh world/scenarios/<scenario>.json <same-run-name> 2
```

It continues from the last checkpoint, and the budget continues with it. A
run killed hard still writes a final checkpoint on the way down, so almost
nothing is lost but the ticks themselves.

---

## 6. Things that actually went wrong, and the fix

| symptom | cause | fix |
|---|---|---|
| `REFUSED: another run holds /tmp/kasukabe-runseries.lock` | a killed run left the lock | `rmdir /tmp/kasukabe-runseries.lock` |
| `15/16 passed — 1 blocking, run refused` | a preflight check failed | fix it, or `KASUKABE_SKIP_PREFLIGHT=1` to start anyway — read which check first |
| `Prompt size is sane [✗] 66 KB` | unbounded working-memory replay in `learning_centre` (inside the Law 1 freeze) | known; skip preflight, watch the budget |
| cameras die instantly, `unrecognized arguments` | launcher and `record_ui.py` disagree on a flag | a test now checks the launcher's flags against the recorder's real parser |
| cameras quit mid-run, "world stopped answering" | `--watch-misses` defaults to 3, a hair trigger under six Chromes | raised to 120 in the launcher |
| run ends with no report and no `sessions/` | the process was killed before the launcher's last steps | regenerate — §4 |

---

## 7. What every town now starts with

Genesis seeds three things that used to be empty, because a town that has to
bootstrap its own supply chain never gets past wheat:

- **every resident** — a one-day larder (`bread 2, apple 1`). Day 0 is covered;
  by day 1 the town has to feed itself, which is the thing being measured.
- **every shop** — opening stock *and* a price board, with a **bid** side so a
  producer has somewhere to sell. Stock with no price is as dead as no stock.
- **the construction yard** — timber, planks, ore and tools, so a crew can
  break ground without waiting on a supply chain that has no buyers yet.

A pharmacy gets nothing on purpose: it sells a service, and there is no
medicine good to shelve.

This changes the initial conditions. **Runs before and after are not
comparable on the settlement or meals gates**, and the report should say which
side of the line a run falls on.
