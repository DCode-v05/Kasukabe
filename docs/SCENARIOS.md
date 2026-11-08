# Scenarios: when a run starts, and what is already true when it does

*Written 2026-09-23. The code is `app/world/scenario.py`, the files are
`app/world/scenarios/*.json`, and the launcher is `app/run-scenario.sh`.*

## Why

Every run this project has made booted the same way: the default town, at tick
0, nothing decided, and then a wait for the thing being measured to come round.
The first election is counted at tick 432 — three game-days of thinking before
the question is even asked, which at the measured $2.27–2.88 a resident-day is
$129–163 at twenty residents.

A scenario moves the question to the front of the run. It says which cast, which
tick the world starts on, when it stops, when the election opens, how long its
phases are, and **who is already sitting in government when the first tick
runs**. An election defended by incumbents asks a sharper question than an empty
one — will the town keep them? — and a scenario can ask it inside the first
game-day, for $15–20.

## The file

```json
{
  "name": "election-at-dawn-8",
  "description": "one line, for the report",

  "world": {
    "genesis": "world/genesis_8_funded.json",
    "start_tick": 36,
    "stop_tick": 150,
    "budget_cap_usd": 19.0,
    "max_game_days": 2,
    "cadence": 0.3,
    "tick_interval_s": 0
  },

  "charter": {
    "candidacy_window_hours": 4,
    "campaign_period_hours": 4,
    "voting_window_hours": 4,
    "election_interval_hours": 24,
    "pm_seats": 1,
    "minister_seats": 2,
    "suffrage_min_age": 18
  },

  "election": { "opens_at": "start" },

  "government": {
    "incumbents": [
      {"agent_id": "a0047", "seat": "prime_minister"},
      {"agent_id": "a0090", "seat": "minister"}
    ]
  },

  "recording": {"url": "...", "interval_s": 2, "fps": 30, "width": 1920, "height": 1080},
  "report": {"out": "../docs/runs/series/election8"}
}
```

Everything but `name` and `world` is optional, and every block left out is the
town as it has always booted.

- **`world`** is the run's shape. `genesis` is resolved from the working
  directory or from `app/`. `cadence` is how often a resident takes a turn
  (0.3 = every third tick). `budget_cap_usd` becomes the runner's cap; the
  launcher gives the broker $5 more, so the runner's governor is what stops the
  town.
- **`charter`** is seeded at **version 0**, not amended afterwards: a town whose
  election windows changed at version 1 would read as having amended its own
  constitution before anybody woke up. Hours, not days, because the point is an
  election that fits inside one game-day — six ticks to the game-hour.
- **`election.opens_at`** is `"start"` (the tick the run begins), `"genesis"`
  (tick 0, the old behaviour) or a tick. An opening before the run starts is
  refused: candidacy nobody can stand in is the bug this file exists to prevent.
- **`government.incumbents`** fill the seats before the first tick. They are
  marked `caretaker` and given no `terms_served`, exactly as GROW-12's caretaker
  is: nobody voted for them, so DESIGN section 10's regime-transition counts
  must not read them as a term. They still act, and they may stand in the
  election they are running.

## What it refuses, before a cent is spent

All of it at load time, every problem in one message: a stop tick before the
start; a cadence outside (0, 1]; a cap of zero; election windows that total more
than the gap between elections; more incumbents than there are seats; an
incumbent who is not in the cast, or is under the charter's own suffrage age; an
unknown key anywhere in the file (which catches typos).

## Running one

```
cd app
CONFIRM_KEY_ROTATED=1 ./run-scenario.sh world/scenarios/election_at_dawn_8.json
```

It starts the broker (:8200), the runner and its embedded world (:8100), the
town viewer (:8400), the observatory (:8500) and the camera, stops them
together, and writes the report — which opens with the checklist scored from the
trace, followed by the scenario's own description, so nobody compares two runs
that began in different situations. `scenario.json` is copied into the output
directory beside the report and the video.

Rehearse it free first — the same world and viewer, mock residents, no models:

```
.venv/bin/python scripts/rehearse_ui.py --scenario world/scenarios/election_at_dawn_8.json \
  --stage-election --tick-s 1.6
```

## What changed for runs that use no scenario

One thing, and it was a bug: `seed_genesis` scheduled the first election at tick
0 whatever tick the world started on, so a town woken at dawn had already slept
through 36 ticks of its own candidacy window. The election is now scheduled from
the clock the run starts on. For the 20- and 50-resident plans that moves the
count from tick 432 to 468, still well inside their stop tick of 590.

## The shipped scenario

`world/scenarios/election_at_dawn_8.json` — eight residents (all adults, from
`genesis_8_funded.json`), a government of three already sitting, an election
held in one game-day, and a stop past the day boundary at tick 144 where the
first tax is charged and government wages become payable.

| | |
|---|---|
| Ticks | 36 → 150 (0.79 game-days) |
| Election | candidacy 36–60, campaign 60–84, voting 84–108, **counted at 108** |
| Expected cost | **$14.40–18.30** at $2.27–2.88 a resident-day |
| Cap | $19 (broker $24) |
| Wall-clock | about 1–1.5 hours at ~40 s a tick |
| Memory | 8 × 64 MB ≈ 512 MB |
| Wallets | $3.50 each, treasury $972 — the $1,000 invariant holds |

Rehearsed free on 2026-09-23: four candidates (two incumbents, two newcomers),
seven ballots, counted at tick 108, a regime change recorded, tax charged at the
day boundary, and the video written. What the residents will actually do is
theirs.
