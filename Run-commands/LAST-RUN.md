# Last run: `trial-day2-20`

Twenty residents, an election on game-day 2, a $20 cap — and the first live
test of the seeded economy.

Full report: [`docs/runs/series/trial-day2-20/report.md`](../docs/runs/series/trial-day2-20/report.md)

## How it ended

**It ran out of ticks, not money.**

| | |
|---|--:|
| metered spend | **$13.44** of $20.00 |
| unspent | **$6.56** |
| ticks | 325 → **396** (`stop_tick` was 396) |
| spend governor | **never tripped** |

The scenario was planned to stop at tick 396 and it reached it. The budget
was the ceiling, not the brake — so the run is complete on its own terms,
with a third of the money unused. To spend the rest, raise `stop_tick` (or
`RATE` in the planner, which buys more ticks for the same dollars).

It was **cut and resumed once**, at tick 366, when the session that started
it tore down. The trace is continuous; the town was paused. The budget
carried across the resume, as designed — the burned balance lives in the
checkpoint.

## What it found

**14 of 42** run-decidable checklist items held, in half a game-day.

### The seeded economy moved

| item | election30 (no seeding) | this run |
|---|--:|--:|
| ECON-4 meals per resident-day | 0.43 ✗ | **3.14 ✅** |
| ES4 settlements per game-day | — | **20.3 ✅** |
| ECON-2 goods move for sim daily | ✗ | **✅** |
| ECON-14 a posted price changes | 0 of 56 ✗ | **2 of 11 ✅** |
| ET1 $/resident-day | $1.72 | **$1.36** |

- **8 settlements, 7 across a shop counter** — 10 iron_ore, 10 apple, 8 bread,
  4 iron_tool, 48 sim of value
- `buy` ×7, `sell` ×2, `post_price` ×11 with **2 repricings** — the first
  price discovery this project has recorded
- `cook` reached; `craft` ×17, `eat` ×31, `harvest` ×36
- `mine` ×20, `fell_timber` ×32 — producers selling into standing bids

### The incumbent lost

| | |
|---|---|
| new PM | **Delia Okonjo (n16)**, 49, pharmacist — 3 votes |
| demoted to minister | **Ainsley Holloway (n02)**, 42, trader — 1 vote, the sitting PM |
| turnout | 4 voters, all ordinary residents, none of them candidates |

The first time a sitting Prime Minister has been voted out here. `civic12`
gave the first incumbent to hold a seat; this gives the first to lose one.

### Not held, and worth saying

- **ECON-5** — 0 hires, 0 wages paid. The employer notice naming `set_wage`
  and `pay_wage` did not move it in 71 ticks. Unproven, not disproven.
- **ECON-11** — 20 tax charges, 0 treasury outflows. The treasury still only
  takes money in.
- **G6** — 48% of turns changed the world, just under the 50% bar.
- **ET1** — $1.36 against a $0.20 target: better than every previous run and
  still nearly 7× the bar.

## Caveats

**The seeding changed the initial conditions.** Runs before and after are not
comparable on the settlement or meals gates. Every figure above that beats
election30 is a comparison across that line, and should be read as such.

**One resumed run is not a clean run.** The town was paused at tick 366 and
restarted from a checkpoint.

## Reproduce it

```bash
cd app
./run-scenario.sh world/scenarios/a_trial_day_two_20.json trial-day2-20 3
```

or let the planner write its own equivalent:

```bash
cd app
./run-sim.sh 20 20
```
