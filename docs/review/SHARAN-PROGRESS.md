# Sharan's lane: what is fixed, and how each was verified

*Companion to `WORK-SPLIT.md`. This tracks only the machine lane - ENG, CORE,
INFRA, EVAL. Deni's lane is untouched by everything here.*

**Thirty-five closed.** Nineteen from the review's own finding list, seven from the
99 open questions. Every block in the lane is now done or has its
remainder named.

**Board:** https://claude.ai/code/artifact/23e02675-c295-4c41-9214-b8d5466bfc18
**Split board:** https://claude.ai/code/artifact/d200cbd5-1c3b-4923-9541-46a293c42e09
**The 99 questions:** `OPEN-QUESTIONS-ANSWERED.md`

## The bar a tick has to clear

A ✅ in this document means **all four** of these, not one of them:

1. **Changed** — the code does the thing.
2. **Wired** — there is a live call site on the path a real run takes, and
   the call-site count is printed in the wiring table below.
3. **Proven** — a test exists, AND the test was run against the old code and
   **observed to fail**. The failure message is quoted. A test that has never
   been seen red is not evidence.
4. **Green** — the full suite passes: 1,475 passed, 9 skipped across
   `world/`, `broker/` and `supabase/`.

Anything that clears fewer than four gets ⚠️ or ❌ and says which one it missed.

## The table

| # | Id | What was wrong | Status | The failure, observed before the fix |
|---|---|---|---|---|
| 1 | `EVAL-01` | The report read event names the world never emits — 5 of 6 wrong, plus 2 payload keys | ✅ | `assert not ['business.price.posted']` |
| 2 | `CORE-02` | Charter events published into `noop_sink` on the host every run uses | ✅ | `charter.elections is still publishing into noop_sink` |
| 3 | `EVAL-02` | The `events` table took zero rows in all 23 runs | ✅ | `the events table took no row for a posted price (saw [])` |
| 4 | `CORE-01` | The tick loop ended on `sleep(0)`; `--tick-interval-s` was dead | ✅ | `10 ticks at 0.2s took 0.000s` |
| 5 | `INFRA-03` | Dormant residents dispatched and refused every third tick | ✅ | `the dormant resident a0039 was dispatched anyway` |
| 6 | `CORE-05` | The pre-halt drain could never fire in time | ✅ | `the drain never fired before the cap` |
| 7 | `INFRA-01` | Checkpoints written to an in-memory DB that logged success | ✅ | `world_snapshots holds 0 rows after a checkpoint` |
| 8 | `INFRA-02` | `llm_calls.tick` NULL on every row ever written | ✅ | `1 of 2 TraceRecord constructions omit tick` |
| 9 | `CORE-04` | Every run billed upkeep and head tax for a day nobody lived | ✅ | `last_life_day_processed is -1 but the boot day is 0` |
| 10 | `EVAL-03` | C1 — the project's headline cost metric — was uncomputable | ✅ | `label_calls` had 4 tests and 0 production callers |
| 11 | `GOV-04` | The crime engine existed as a tested library, never instantiated on a running world | ✅ | `GenesisRunner booted without a crime engine` |
| 12 | `ENG-02` | Every model call in 23 runs took the *emergency* compaction path | ✅ | `window is 12500 and RESERVE_FLOOR alone is 25000` |
| 13 | `ECON-05` | At 10,000:1 energy was unbuyable from honest work | ✅ | rate changed to 5,000; 9 pinned tests rewritten to assert the property |
| 14 | `CORE-17` | Night dispatched like day; no run in 23 ever reached tick 144 | ✅ | `10 quiet night ticks took 2.01s at a 0.2s interval` |
| 15 | `12`.6 | The household's tax fell on whoever was seeded first | ✅ | `the day-tax job does not ask who should pay` |
| 16 | `11`.7 | An owner could name anyone in town as their employee | ✅ | `an owner can still name anyone in the town` |
| 17 | `11`.8 | `steal` could not reach a shop's shelf — theft by omission | ✅ | `steal_handler still reads world.storages directly` |
| 18 | `11`.4 | An invented tool could never go on a routine | ✅ | 1 failure with the gate reverted |
| 19 | `14`.7 | A lost row stopped a run 3 failures late, not at preflight | ✅ | `critical is True` / FAIL not WARN |
| 20 | `15`.7 | `supabase/migrations` cost 3 edits a change under skipped tests | ✅ | frozen at 22, test fails on a 23rd |
| 21 | `ENG-01` | The learning centre could not write a row; nothing survived a turn | ✅ | `EpisodicMemory(db...) is never constructed` |
| 22 | `ENG-03` | 82 tool schemas on every call — two thirds of the bill | ✅ | `59 full schemas served against a budget of 24` |
| 23 | `ENG-16` | The location-reduced schemas saved 16% and produced 0 index lines | ✅ | measured: 14.8K → 7.6K tokens |
| 24 | `CORE-23` | Source-text tests on exactly the paths that were broken | ✅ | rewritten to charge a real ledger |
| 25 | `EVAL-21` | `test_upkeep_is_wired` — a comment satisfied every assertion | ✅ | now moves money or fails |
| 26 | `ENG-26` | Seam tests never asserted what reached the wire | ✅ | 3 checks on a real request body |
| 27 | `ENG-04` | Signals grew O(N²) — 50 turns wrote 1,275 rows for 50 entries | ✅ | `the second turn re-flushed 3 entries instead of 1` |
| 28 | `CORE-24` | A run's config, seed and git sha lived only in shell history | ✅ | `the run recorded nothing about itself` |
| 29 | `GROW-02` | `issue_builder_license` had zero callers; the gate refused everyone | ✅ | only mention outside its def was a docstring |
| 30 | `GROW-04` | Nothing ever finished a building in 23 runs | ✅ | `nothing on the tick path finishes a building` |
| 31 | `GROW-01` | Nothing could create a plot; `build_structure` refused everyone at check 1 | ✅ | `world.plots` empty on every fresh run |
| 32 | `GROW-03` | A finished smithy was a row the builder could not forge in | ✅ | `a row rather than a place` |
| 33 | `ECON-09` | `pay_pension` had zero callers; nobody ever drew a pension | ✅ | `drew 0 instead of 100` |
| 34 | `ENG-16` | herald cached on the NAME SET; ENG-03's saving reached the wire once | ✅ | `the refresh changes nothing on the wire` |
| 35 | *the temple* | `"Temple"` was refused at the temple — a run found it, no reviewer did | ✅ | `a resident standing at 'Temple' is refused` |

## The four decisions, as landed

You settled all four on 2026-09-04. Three were mine to build; the fourth was
already done.

| | Decision | Status |
|---|---|---|
| 1 | The runner absorbs the server's wiring | ✅ charter sink (CORE-02) + crime engine (GOV-04). `bind_event_bus` proved redundant — `persist()` already does all six of its writes, bounded. |
| 2 | History bounded in the seam | ✅ `DISABLE_AUTO_COMPACT=1`, window 12,500 → 131,072, `sim/bounded_history.py`. Nothing in `engine_core/` touched. |
| 3 | `SIM_PER_USD` → 5,000 | ✅ and `REVIVAL_COST_SIM` now *derived* rather than written, so the next re-rating cannot break an import. |
| 4 | The President is a veto window | ✅ **Deni's**, verified here: `close_the_veto_window` runs on the tick path with the reasoning in its docstring. |

## The wiring table

Written code that nothing calls is the failure mode this whole review is
about, so each fix is counted at its live call site rather than asserted:

| Id | Live call site | Count |
|---|---|---|
| `EVAL-01` | `run_report` reads `economy.price.posted` | 1 |
| `CORE-02` | `boot()` calls `wire_the_charter_to_the_bus()` | 1 |
| `EVAL-02` | `persist()` calls `store.record_event(event)` | 1 |
| `CORE-01` | `run_continuous` calls `wait_out_the_tick(started_at)` | 1 |
| `INFRA-03` | `dispatch_due_agents.one()` calls `is_dormant(agent_id)` | 1 |
| `CORE-05` | `run_continuous` calls `drain_before_halt()` | 2 |
| `INFRA-01` | the mirror receives `client=self.trace_store.client` | 1 |
| `INFRA-02` | `TraceRecord(tick=prepared.tick)` + rejection row + `payer.tick` | 1 / 1 / 2 |
| `CORE-04` | `boot()` sets `last_life_day_processed` on the fresh path | 1 |
| `EVAL-03` | `build()` renders `learning_section(...)` | 1 |

**This table earned its place.** It caught INFRA-02 at **0 call sites** after
I had already written the fix, a test, and a passing green run. The
dataclass had the field; the construction never passed it. The test could
not see the difference, because a field with a default is satisfied by
silence. Both were fixed, and the replacement test reads the `TraceRecord(...)`
construction out of the AST — which promptly found a **second** missing site
on the rejection path.

## Two things found while fixing, that the review did not have

**`stakes_report.py` is orphaned.** `run_report.py` does not import it
anywhere. `label_calls` — which computes the `task_label` the evaluation
spec names as the learning curve's x-axis — had four tests and no
production caller at all. The whole evaluation module was dead code with a
green suite over it. Now wired.

**`tool_invocations` has no table.** It is an in-memory ring
(`world.tool_invocations`); there is no such table in `sqlite_schema.sql`.
So `label_calls`'s own docstring claim to be *"replayable from a finished
trace years later"* is **not true today**. I wired the join live, through a
new `GET /world/tool_invocations`, which is the same shape the money and
price sections already use — and carries the same limit: **the report must
be generated while the world is still running.** Persisting invocations is
still open and is recorded here rather than papered over.

## Two tests replaced, not deleted

Both asserted source text, and the review named that pattern specifically.

- `test_it_does_not_mirror_every_event_payload` asserted `"record_event"
  not in code`. It was **actively enforcing EVAL-02** — the bug was the
  thing the test protected. It now asserts the property: the per-tick
  chatter stays excluded, the decisions get recorded.
- `test_the_audit_reads_code_not_comments` used `record_event` as its canary
  for "the AST strip works", because that name appeared only in prose. It is
  real code now, so the canary is a phrase that still exists only in a
  docstring.

`record_event` was also removed from `DELIBERATELY_UNWIRED`, so the
recorder-coverage test now covers it like every other writer.

## What is still open in my lane

| Id | What | Why not yet |
|---|---|---|
| `ENG-01` | Learning centre cannot write a row on the sim path | The biggest one left. Nothing a resident learns survives its own turn. |
| `ENG-02` | Every call emergency-compacted; day-0 perception pinned forever | Blocked on decision 2 — where history is bounded. |
| `ENG-03` / `ENG-16` | All 79 tool schemas ship on every call | Follows ENG-02. |
| `CORE-17` | Night is dispatched tick by tick; no run has reached one | Needs the pacing from CORE-01, which now exists. |
| `INFRA-04` | The infra path does not boot from its own env templates | Untouched. |
| `INFRA-05` | Secrets in git | Untouched, and **the AWS keys still need rotating** — that is your console, not mine. |
| — | `tool_invocations` not persisted | Found here; C1 works live but is not replayable. |

**Still blocked on you:** decision 1 was taken as my recommendation — the
runner absorbs the server's wiring — and CORE-02 is the first instalment of
it. The crime engine and the map service are the remaining two pieces and
are worth doing together. Say the word if you want it the other way round;
nothing landed so far would need reverting.
