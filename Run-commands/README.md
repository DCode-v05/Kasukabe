# Run commands

Everything needed to start a simulation, watch it, and get the report back.

| file | what |
|---|---|
| **README.md** (this) | the commands, copy-paste |
| **[FULL-REFERENCE.md](FULL-REFERENCE.md)** | every knob explained, what each cast file holds, what went wrong before and the fix |
| **[LAST-RUN.md](LAST-RUN.md)** | the most recent run: what was asked for, what it cost, what it found |

---

## Start a run

Say how many residents and what it may spend. Everything else follows.

```bash
cd app
./run-sim.sh 20 20
```

```bash
./run-sim.sh 20 20 my-run-name     # name it yourself
./run-sim.sh 12 15                 # 12 residents, $15
./run-sim.sh 8 10                  # 8 residents, $10
```

## See the plan without spending anything

```bash
cd app
PLAN_ONLY=1 ./run-sim.sh 20 20
```

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

**A plan whose vote is never reached is refused, and nothing is spent:**

```
== planning: 50 residents, $20
  ** THE VOTE IS NEVER REACHED on this budget. Raise it, or cut the cast:
     at this rate the election alone needs $21.56.
   REFUSED: this plan never reaches its own vote. Nothing was spent.
```

## Knobs

```bash
DAY=1 ./run-sim.sh 20 20          # start on game-day 1 instead of 2
RATE=1.40 ./run-sim.sh 20 20      # a cheaper estimate -> a longer run
HOURS=3 ./run-sim.sh 20 20        # wall-clock ceiling (default 6)
NO_ELECTION=1 ./run-sim.sh 20 20  # no vote, just the town
```

| knob | default | what it does |
|---|---|---|
| `DAY` | 2 | game-day to start on. 144 ticks to a day; dawn is tick 36 |
| `RATE` | 2.30 | $/resident-day used for planning. Measured: 0.96–2.49 |
| `HOURS` | 6 | wall-clock ceiling |
| `NO_ELECTION` | off | skip the vote |
| `PLAN_ONLY` | off | plan and stop |

## Watch it live

```
http://127.0.0.1:8400/township-v2.html      the town
http://127.0.0.1:8500                       observatory
http://127.0.0.1:8100/world/state           world state (JSON)
http://127.0.0.1:8200/admin/spend           spend so far (JSON)
```

Add `?book=<panel>&roofs=<on|off>` to the viewer:

```
?book=town&roofs=on          the map, with buildings
?book=town&roofs=off         the map, people working indoors
?book=groupchat              live chats
?book=election               the vote
?book=money                  treasury and wallets
?book=govern                 the government
```

Panels: `preflight` `election` `workshop` `council` `groupchat` `talk`
`authored` `people` `money` `govern` `health` `town`.

## What it costs

**residents × game-days × $1.70**, roughly. A game-day is 144 ticks.

| run | residents | $/resident-day |
|---|--:|--:|
| election8 | 8 | $2.28 |
| civic12 | 12 | $2.49 |
| election30 | 30 | $1.72 |
| trial-day2-20 | 20 | **$1.36** |

Wall clock is about **1.5 minutes per tick** with six cameras running.

```
20 residents, 2 whole game-days  = 40 resident-days  ≈ $69
20 residents, half a game-day    = 10 resident-days  ≈ $17
```

## Check spend mid-run

```bash
curl -s http://127.0.0.1:8200/admin/spend | python3 -m json.tool
```

Or what was actually billed, from the trace:

```bash
cd app && .venv/bin/python -c "
import sqlite3
d = sqlite3.connect('file:data/<run-name>.db?mode=ro', uri=True)
print('\$%.2f' % ((d.execute('select sum(cost_nano_dollars) from llm_calls').fetchone()[0] or 0)/1e9))"
```

## What you get back

`docs/runs/series/<run-name>/`

```
report.md              the run report, opening with the 42-item checklist
video/<view>/run.mp4   one video per recorded view
sessions/*.jsonl       one per-turn log per resident
scenario.json          exactly what was run
runner.log broker.log record-*.log
```

## Regenerate a report

If a run was cut before its report was written:

```bash
cd app
.venv/bin/python -m world.run_report \
  --db data/<run>.db --home-root ~/kasukabe-homes/<run> \
  --out ../docs/runs/series/<run>/report.md

.venv/bin/python -m world.trial_section --db data/<run>.db \
  >> ../docs/runs/series/<run>/report.md
```

## Resume a run that was cut

```bash
cd app
RESUME=1 ./run-scenario.sh world/scenarios/<scenario>.json <same-run-name> 2
```

The budget continues with it — the burned balance is in the checkpoint, so a
resumed run does not get a fresh $20.

## Run a scenario file directly

`run-sim.sh` writes a scenario and hands it to the launcher. To run one you
wrote or edited by hand:

```bash
cd app
./run-scenario.sh world/scenarios/<scenario>.json <run-name> <max-hours>
```

## When it will not start

| it says | do this |
|---|---|
| `REFUSED: another run holds /tmp/kasukabe-runseries.lock` | `rmdir /tmp/kasukabe-runseries.lock` |
| `15/16 passed — 1 blocking, run refused` | read which check failed, then `KASUKABE_SKIP_PREFLIGHT=1` if you accept it |
| `Prompt size is sane [✗] 66 KB` | known — unbounded memory replay in `learning_centre`. Skip preflight, watch the budget |
| `THE VOTE IS NEVER REACHED` | raise the budget, cut the cast, or `NO_ELECTION=1` |

## Which model

```
moonshotai.kimi-k2.5   agent cognition (the default)
deepseek.v3.2          background work, and overflow
qwen.qwen3-coder-480b  third lane, needs an inference profile
```

**Not an env knob.** `MODEL_ALLOWLIST` in `broker/config.py` is a hard
allowlist; any other model id is a 403 by design. All three run through
Bedrock, so **adding GPT or Claude is a code change** — a new lane in
`broker/lanes.py` and its own client. Budget a day, not a config edit.
