# The run artifact: one directory layout, agreed before anything writes it

*INFRA-09 / `15`.8. Agreed 2026-09-04, so the report generator, the
observatory and the exporter read one shape rather than three.*

## The layout

```
runs/<run_id>/
  manifest.json              app + engine commit, genesis sha256, seed,
                             knobs, prices, inference profiles, start time
  kasukabe.db                the trace store: llm_calls, events,
                             utterances, transactions, agents, skills, …
  checkpoints/
    checkpoint_0000000144.json.gz     one per game-day boundary
  engines/
    day-0.tar                every resident's SEPTEMBER_HOME, tarred at
                             the day boundary: brain.sqlite, persona.txt,
                             session_log.jsonl, memory_stream.jsonl
  exports/
    parquet/game_day=0/      llm_calls, events, transactions, utterances
  reports/
    run-<n>-report.md        generated while the world is still live
  litestream/                the replica, if replication is on
```

## Why each piece is where it is

**`manifest.json` at the top, written at boot.** A run whose provenance is
reconstructed afterwards is not reproducible, it is remembered. Written
before the first tick so a crashed run still has one.

**One `kasukabe.db`, not one per subsystem.** INFRA-01 was a mirror writing
to `":memory:"` while the local write succeeded and logged success. One
database per run makes that class of mistake visible: if a table is empty,
it is empty in the one place anyone looks.

**`engines/` as tarballs at the day boundary.** A resident's `brain.sqlite`
is the only record of what it privately knew. It lives in `/tmp` today and
does not survive the machine, let alone the run.

**`exports/parquet/game_day=N/`.** The partition key is `game_day`, which is
why INFRA-02 mattered: with `llm_calls.tick` NULL, `game_day` was NULL, and
this directory came out empty for every run ever exported.

**`reports/` inside the run.** A report that lives beside its trace can be
re-derived from it. One that lives in `docs/runs/` cannot be checked against
anything once the world is gone.

## The one constraint this layout carries

**The report must be generated while the world is still live.** Several of
its sections — money, prices, `tool_invocations` for the C1 join — read the
live world API rather than the trace, because the underlying data is either
in-memory only or has no table. That is a real limitation and it is written
here rather than discovered by someone running the generator on a finished
directory and getting a confident, empty report.

Removing that constraint means persisting `tool_invocations` (open) and
giving upkeep an event of its own (open, EVAL-01 found it emits none).
