*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code and running the suites; every claim cites `path:line`. Finding ids (`EVAL-nn`) are referenced from the cross-cutting documents.*

# Review H — evals, tests, reports and docs: how Kasukabe knows what is true

*Read-only review of `Real` at `5400e94` (checked out as `claude/report-run-evaluation-ir3bix`; `origin/main` is 118 commits behind at `e75fb97`, 2026-08-17). Report prefix `EVAL`. Every claim cites `path:line`; where I infer, I say so. Two test suites were run under a 10-minute cap; results in §5 and Appendix A. I created and then removed one zero-byte artifact the app test-suite itself wrote (`app/data/kasukabe.db`, gitignored); nothing else in the repo was touched.*

---

## 0. Executive summary

The project has an unusually honest evidence *culture* (thresholds fixed in advance, "a green line over nothing is worse than a red one", every fix with a test) and a surprisingly broken evidence *pipeline*. The instruments that are wired read the wrong sources, and the instruments that read the right sources are not wired:

1. **The report that closes every run lies about the economy by construction.** `run_report.py`'s six evaluation-spec sections read the trace's `events` table for event types the world never emits (`business.price.posted` vs the real `economy.price.posted`, `agent.dormancy.entered` vs `health.agent.dormant`, `business.wage.set` vs `business.wage.promised`, `economy.head_tax.charged` which nothing emits, and an upkeep payload key that does not exist). That is why run 23 printed "**Nobody posted a price**" under an operator note counting **12 posted prices** (EVAL-01). Worse, in a genesis run the `events` table is *never written at all*: `record_event` is deliberately unwired and its own test skips it (EVAL-02). So E1–E7, S1–S7, T2–T4, C4 and C5 of the evaluation spec are unmeasurable from any trace this project has produced.
2. **The first compounding quantity cannot be measured.** `llm_calls.tick` is `None` on every row (the broker does not know the tick), `task_label` is a schema column nothing fills, and the world module that computes labels has no caller. The (agent, tick) join that C1 depends on joins on zero (EVAL-03, -04).
3. **The L1 harness measures a prompt no resident receives** (persona off by default, canned observation answers, speech pasted as text, agent ids as names), persists nothing, and covers 14 of 77 tools. Rule 8 ("a tool without L1 is not evidence") therefore disqualifies 63 tools (EVAL-06..08).
4. **Judges 3.2/3.3 and character gates P1–P3 have no driver** and have never been run on a trace (EVAL-09). The control bakery has no production caller (EVAL-15). The `no_learning` arm exists in the seam but no resident in the series cast carries it (EVAL-16).
5. **Reproducibility:** no run manifest, no code hash, no genesis hash, run_id lives only in process memory, checkpoints have never been written (they fire at a day boundary that has never come), per-agent brains are discarded with `/tmp`, and the trace databases behind runs 20–23 are not in the repository. A third party cannot recompute a single number in `docs/runs/run-23-report.md`.
6. **Tests:** app suite (infra ignored) **3,145 passed, 110 failed, 68 skipped** in 85 s; 71 failures and 46 skips are the missing vendored `smallville/` tileset, 9 are DuckDB fetching an extension over the network, ~25 are geography assertions that depend on the map, and 2–3 look real (a named-conversation `say` landing in the wrong thread; the control bakery not answering a directed question). The suite cannot even be *collected* without `TRACE_SQLITE_PATH` set (module-level `create_app()`). The engine suite needs `uv sync --extra dev` before `pytest` will import its conftest; it collects 4,441 tests, does not finish inside ten minutes here, and its sim-seam subset is **116 passed, 1 failed** — the failure being a test that still asserts the old meaning of `learning_ran` (EVAL-28).

Against the operator's target the honest state is: the town has 77 verbs, one morning of proven commerce, and **no instrument that can say whether anyone is thriving, whether the economy is healthy, whether an institution functioned, whether the town grew, whether any goal was pursued, or what a task costs over time.** §5 proposes that suite, table by table.

---

## 1. Inventory

Status legend: **LIVE** = on the production path from `world.run_genesis` / `sim_main.py` / `game.server`; **CLI** = a standalone command an operator runs against a finished run (not on the production path, but has a real entry point); **TEST-ONLY** = only its tests call it; **ORPHANED** = no production or CLI caller.

| Path | Lines | Purpose | Status | Evidence |
|---|--:|---|---|---|
| `app/world/evals/verb_reach.py` | 470 | L1: does the model reach for a verb in the verb's own situation; 37 JSON scenarios; thresholds fixed at L62-67 | CLI (`python -m world.evals.verb_reach`) | `verb_reach.py:469-470`; no importer outside `world/tests/test_verb_reach_harness.py` |
| `app/world/evals/scenarios/**` (14 dirs, 37 files) | 700 | L1 situations for `buy, buy_energy, check_debts, license_tool, pay_wage, post_price, revive, sell, sell_tool, set_intention, set_wage, skillify, teach_tool, trade` | CLI data | `ls app/world/evals/scenarios` |
| `app/world/evals/judges.py` | 195 | L3 judges 3.2 (persona adherence) and 3.3 (energy awareness): rubric, `_ask`, `distribution`, `render`, `stratified` | **ORPHANED** | no `__main__`, no caller; only `world/tests/test_judges.py` imports it |
| `app/world/evals/character.py` | 209 | P1–P3 deterministic character gates (`scan_turns`, `name_exchanges`, `render`) | **ORPHANED** | no `__main__`; only `world/tests/test_character_gates.py` |
| `app/world/evals/behavioural.py` | 434 | B1–B7 human-baseline measures off a completed store | CLI (`python -m world.evals.behavioural <db>`) | `behavioural.py:433-434`; `docs/runs/series/fifty/behavioural.txt` shows it was run once |
| `app/world/evals/answered_or_deflected.py` | 241 | L3 judge 3.1 (answered / deflected / silent), real model, billed to treasury | CLI | `answered_or_deflected.py:674-675`; result quoted in `HUMAN_BASELINES.md §7.1` |
| `app/world/evals/eval_s4.py` | 335 | Build-Spec §4/§5 board (walk completes, lost, fed, new tools, refusal recovery, group chat, settlement) read from `session_log.jsonl` + utterances | CLI script (module-level code; hard-coded `HOMES` at L18) | no importer; runs top to bottom on import |
| `app/loop_report.py` | 321 | G1–G6 gates off a completed store; exit code drives the autonomous loop | CLI | `docs/AUTONOMOUS_LOOP.md`; `runseries.sh` |
| `app/series_report.py` | 139 | One table across series runs; imports `loop_report` | CLI | `series_report.py:430-439`; defaults to `short1..run6` (L544) |
| `app/world/run_report.py` | 1,465 | The per-run document: live world state + trace + session logs; §6 sections at L362-479 and L1070-1081 | **LIVE** (`--watch` during runs; `runseries.sh`, `run50.sh`) | `docs/TEST_RUN.md:11`; every `docs/runs/run-N-report.md` |
| `app/world/stakes_report.py` | 437 | Correct readers for §6 (prices, stakes, wages, time, diffusion, learning) and a second `task_label` | **ORPHANED** | only `world/tests/test_stakes_report.py`; `grep -rn stakes_report app --include=*.py` finds no production import |
| `app/world/task_label.py` | 70 | §4 task label from the cost schedule (`label_for_turn`) | **ORPHANED** | only `world/tests/test_task_label.py`; `broker/trace.py:137` cites it in a comment, never calls it |
| `app/world/control_bakery.py` | ~280 | Scripted control bakery (R1–R7 baseline) acting through `registry.execute` | **ORPHANED** | no reference in `run_genesis.py` or `main.py`; `genesis_20.json` `control_businesses: []` |
| `app/supabase/sqlite_schema.sql` `eval_metrics` table | — | Intended home of eval results | **ORPHANED** | `wiring.py:557` lists it among "columns … nothing" writes; only `research/parquet_export.py:78` reads it |
| `app/research/parquet_export.py` | — | DESIGN §9 per-game-day Parquet export | **ORPHANED** | no call in `run_genesis.py` (`grep parquet` empty); tests need network |
| Docs in scope | | | | see §4 and §7 (drift table) |

Test-suite inventory (files / `def test_` counts, `grep`-based):

| Package | Files | Tests |
|---|--:|--:|
| `engine/tests` | 181 | 4,054 |
| `app/world/**/tests` | 200 | 2,253 |
| `app/broker/tests` | 24 | 346 |
| `app/game/tests` (+`game/console/tests`) | 10 | 143 |
| `app/supabase/tests` | 9 | 137 |
| `app/research/tests` | 2 | 32 |
| `app/tests` (cross-module invariants) | 3 | 10 |

---

## 2. How the project knows what is true today (the live path)

**2.1 Before a run: deterministic tests.** `make test` → `uv run pytest` in `app/` (`app/Makefile:6`). The suite imports `broker.app`, which executes `app = create_app()` at module scope (`app/broker/app.py:335`) and opens `settings.trace_sqlite_path`, default `/data/kasukabe.db` (`app/broker/config.py:285`). On a fresh clone that directory does not exist, so collection aborts before a single test runs (Appendix A, run 1). With `TRACE_SQLITE_PATH` pointed somewhere writable the suite runs in 85 s.

**2.2 Before a tool ships: L1.** `uv run python -m world.evals.verb_reach [tools] --samples N` loads every `scenarios/*/*.json` (`verb_reach.py:107-127`), builds a fixture world with `build_world(genesis_tick=0, seed=1)` (L254), renders a `Perception` with `heard_events=[]`, tick 40, 08:00 (L268-277) through the real `render_perception_text` with a `self_brief` whose `name` is the agent *id* (L281-286), appends recent speech and the open question as plain text (L288-291), and sends `MAX_STEPS=3` rounds to the broker as `government:treasury` with purpose `eval.verb_reach` (L298-379). Observation calls are answered with a canned JSON of the scenario's own facts (L382-397), a world-changing call ends the turn. The system prompt is **empty unless `KASUKABE_L1_PERSONA=1`** (L428-430), for a measured reason recorded in the comment (persona-as-`system` halves the pass count). Scores → `score_samples` (L130-177) → `verdict` against `THRESHOLDS` (L62-67, L180-196). Output is a printed board; nothing is stored. The only recorded run is prose in `docs/runs/run-22-report.md:110-131` ("21 of 37 pass", 5 samples, not the 20 the spec fixes at `kasukabeevaluationspec.md §1.3`).

**2.3 During a run: the runner's own instruments.** `GenesisRunner.attach_persistence` (`run_genesis.py:3202-3300`) opens the trace store at `TRACE_SQLITE_PATH` and subscribes `persist()` to `"*"`, which routes **only** `record_transaction_if_new`, `record_utterance_if_new`, `record_skill_if_new`, `record_invented_tool_if_new`, `record_memory_if_new` (L3264-3275). `record_event` is excluded on purpose (L3219-3222) and the test that audits recorders skips it (`world/tests/test_every_recorder_is_wired.py:27-31,67`). The broker writes `llm_calls` with `model, requested_model, region, call_class, purpose` (`sqlite_schema.sql:333-337`) but `tick=None` (`broker/trace.py:34-43,128`) and `task_label=None` (L139, L631). Each engine writes `session_log.jsonl` under `/tmp/kasukabe-agents/<id>/` (`docs/TEST_RUN.md:284-296`). `run_report.py --watch` rewrites `docs/runs/run-N-report.md` every 5 minutes from **three sources**: the live world API (`/world/state`, `/world/utterances`, `/world/authored`, `/world/vitals`, `/world/refusals`, `/agents`; `run_report.py:775-790`), the trace store (`llm_calls`, `events`; L51-77, L1045-1081) and the session logs (L659-706). The hand-written operator note above `LIVE_MARKER` survives regeneration (L1340-1369).

**2.4 After a run: gates and baselines.** `loop_report.py data/<run>.db` grades G1–G6 (`loop_report.py:22-28, 290-297`) from `utterances`, `transactions`, `businesses`, `agents`, `llm_calls`; `behavioural.py` grades B1–B5 and reports B6–B7 from the same tables plus `events` for B2 (`behavioural.py:172-196`, which returns `-1 = no evidence` when `events` has no `agent.utterance.spoken` rows — i.e. in every genesis run). `series_report.py` lines the gate numbers up across `data/<name>.db` and `docs/runs/series/<name>/sessions/*.jsonl` (L444-502). `answered_or_deflected.py` spends money to split "spoke back" into answered/deflected.

**2.5 What never runs.** `judges.py`, `character.py`, `stakes_report.py`, `task_label.py`, `control_bakery.py`, `eval_metrics`, `parquet_export.py` (Inventory).

---

## 3. Findings

Severity: P0 blocks the target or corrupts data/money; P1 major gap; P2 defect; P3 polish. Layer: seam / world / registry / frontend / broker / engine-finding.

### EVAL-01 (P0, world, S) — `run_report.py` reads event types the world never emits; the economy sections are false by construction
- `app/world/run_report.py:1070` `prices_section(_events("business.price.posted"))` — the world emits `economy.price.posted` (`app/world/tools/primitives/commerce.py:642`; `stakes_report.py:510` and `state.py:1048` agree).
- `run_report.py:1076` `dormancies=_events("agent.dormancy.entered")` — the world emits `health.agent.dormant` (`app/world/life/death.py:92`; `retrieval_depth.py:821`).
- `run_report.py:1080` `promised=_events("business.wage.set")` — the world emits `business.wage.promised` (`app/world/tools/primitives/labour.py:127`).
- `run_report.py:1074` `head_tax=_events("economy.head_tax.charged")` — nothing emits it; `head_tax.py:436,511` emit `economy.head_tax_debt.accrued/repaid`.
- `run_report.py:406,409` sum `r.get('amount', 0)` on upkeep events whose payload carries `total_sim` (`app/world/life/upkeep.py:305-313`) — would print 0 sim even with events present.
- Evidence in the record: `docs/runs/run-23-report.md:32` "12 posted prices" (operator note) vs `:684` "**Nobody posted a price.** Read from `events`…"; `:694-696` "No wage was ever promised" is likewise unfalsifiable.
- Why it matters: this is the document every run ends on and the only record of runs 20–23. Every "reported" E/S figure in `kasukabeevaluationspec.md §2` is printed as zero regardless of the run.
- Fix: delete the three hand-rolled readers in `run_report.py:362-479` and call `stakes_report.prices/stakes/wages/time_spent/diffusion` (which use the right names, `stakes_report.py:509-706`); add `world/tests/test_report_reads_the_events_the_world_emits.py` that imports each emitter's literal (or better, move event names to constants in `world/core/events.py` and import them on both sides). The existing `test_report_sections.py` passes fixture dicts with the wrong names and so can never catch this.

### EVAL-02 (P0, world, M) — in a genesis run the `events` table is never written, so nothing that reads it can report anything
- `app/world/run_genesis.py:3212-3222`: "Deliberately NOT record_event(), which mirrors every WorldEvent payload into `events` - that is an unbounded write proportional to tick count". `test_every_recorder_is_wired.py:27-31` `DELIBERATELY_UNWIRED = {"record_event"}`; the parametrised test skips it (L65-67) so the board is green.
- Consumers of `events` in a genesis run: `run_report.py:1045-1081` (all §6 sections), `stakes_report.py` (every function), `behavioural.py:172-196` (B2, returns "no evidence"), `answered_or_deflected` (none), `eval_s4` (none). The tool spec Part E says the opposite: "The world's event bus is bound to the TraceStore in every run mode, including embedded" (`docs/kasukabetoolspec.md:418-421`).
- Why it matters: DESIGN §2's "the trace is a complete causal record"; ROADMAP T3's gate ("writes rows to agents, **events**, utterances, transactions, memories, relationships and skills"); every stakes/price/wage/dormancy/energy/diffusion figure.
- Fix (world): re-attach `record_event` behind an allow-list of domains (`economy.*`, `business.*`, `health.*`, `politics.*`, `crime.*`, `agent.intention.*`, `agent.continuing.*`, `agent.utterance.spoken`, `world.correction.*`) and drop perception-scale noise; the 21 GB store in `TEST_RUN.md:670-688` was request bodies, not world events. Pin with a test that boots `GenesisRunner` in mock mode for 10 ticks with a `post_price` and asserts an `economy.price.posted` row in `events`.

### EVAL-03 (P0, seam + broker, M) — `llm_calls.tick` is always `None` and `task_label` is never written; C1 cannot be computed
- `app/broker/trace.py:34-43` ("broker has no access to world's current tick today"), `:128 tick: int | None = None`, `:139 task_label: str | None = None`, `:629-631` written as-is. `sqlite_schema.sql:326-328` declares the column "from the tools the turn called (world/task_label.py), never by a model" — nothing calls `world/task_label.py`.
- Observed: `docs/runs/series/fifty/gates.txt` line 4 `last tick 0` on a 2,288-call run.
- `stakes_report.label_calls` (L109-147) joins `llm_calls` to invocations on `(agent_id, tick)`; with `tick=None` every call keys to `(agent, 0)`. `learning()` (L423-437) buckets by `tick // TICKS_PER_DAY` — every row is day 0.
- Contradiction in the docs: `STAKES_TIME_COMPOUNDING.md §4.2` says the column was deliberately *not* added; the schema has it.
- Fix: the seam's herald adapter sends `X-Kasukabe-Tick` and `X-Kasukabe-Turn-Id` on every call (the same header path `X-Kasukabe-Purpose` was meant to use; `engine/src/sim/` currently sends neither — `grep -rn X-Kasukabe engine/src/sim` is empty); the broker stores both; a post-run job (`world/task_label.py`) fills `task_label` by joining to a new `tool_invocations` table (EVAL-25). Until then C1, T2, T5-per-day and every per-day learning figure are unmeasurable.

### EVAL-04 (P1, world, S) — two task-label implementations that disagree, both orphaned
- `app/world/task_label.py:359-405` (`label_for_turn`, categories from `DEFAULT_SCHEDULE`, observation set includes `move`, `rest`, `search_memory`) vs `app/world/stakes_report.py:55-83` (`task_label`, hard-coded `LABOUR_TOOLS`/`TRADE_TOOLS`, look set without `move`/`rest`). A turn `["move","look_around"]` is `look` in one and `idle` in the other.
- Fix: delete the copy in `stakes_report.py`; `label_calls` imports `label_for_turn`. Effort S.

### EVAL-05 (P1, world, S) — `stakes_report.py` is the correct instrument and has no caller
- Only `world/tests/test_stakes_report.py` imports it. `run_report.py` re-implemented three of its sections wrongly (EVAL-01) and never implemented Time, Diffusion, Learning or Business at all (grep `time_spent|diffusion|learning(` in `run_report.py`: none).
- Fix: `run_report.build()` calls the six `stakes_report` readers over the `events` table (after EVAL-02) and `llm_calls` (after EVAL-03).

### EVAL-06 (P1, world + seam, M) — the L1 harness measures a prompt no resident is ever given
- No system prompt by default (`verb_reach.py:407-430`), while every live turn carries persona + law as a STABLE section (`SYSTEM_REVIEW.md §1.2`). The comment itself concedes "a bare `system` string is a different prompt wearing the same words".
- `heard_events=[]` and speech appended as `"Said here just now:"` text (L268-291) instead of the real hearing path that renders vocatives, `involves_self`, open questions and awaiting-reply lines.
- `self_brief={"name": scenario.resident, ...}` (L281-286) tells the model it is "n09"; the run tells it "You are Corwin Thorne, the town's forester" (`TEST_RUN.md:55-63`).
- Scenario `location` strings use the `business:b_grocery` / `home:h_vance` spellings — the very two-geography defect the brief lists.
- Observation calls are answered with canned JSON (L382-397), not the real `look_around` result, so a description that depends on what `look_around` says (e.g. posted prices at the counter) is never tested.
- Why it matters: rule 8 (`kasukabeevaluationspec.md §7`) makes L1 the gate for shipping a tool; a gate on a synthetic prompt gates the wrong thing, and the harness's own numbers show the prompt matters (21/37 vs 13/37).
- Fix: make L1 use the engine's own composition — boot one real sim engine against a fixture world with a fake broker, as `engine/tests/test_sim_pinned_boot.py` already does, and send the scenario as the tick; or, cheaper, call the runner's real `self_brief`/`heard_events` builders (`retrieval_depth.py`) and load `persona.txt` the way `pinned_boot` does. Keep the current mode as "description-only" and label its output so.

### EVAL-07 (P1, world, S) — L1 results are not persisted; `eval_metrics` is unused
- `verb_reach.run()` prints and returns an exit code (L400-451). The only record of any L1 run is `docs/runs/run-22-report.md:110-131` at 5 samples. `eval_metrics` exists in the schema (`sqlite_schema.sql:606`) and `wiring.py:557` lists it among tables nothing writes.
- Fix: write one `eval_metrics` row per scenario (`metric_payload` = ScoreCard + model + samples + scenario file hash + git sha), and a `docs/evals/L1-<date>.md` board from it.

### EVAL-08 (P1, registry/world, M) — L1 covers 14 of 77 tools; the spec's own backfill list has no scenarios
- Scenario dirs: `buy buy_energy check_debts license_tool pay_wage post_price revive sell sell_tool set_intention set_wage skillify teach_tool trade` (37 files). The spec's §1.6 backfill list (`trade, sell, buy, start_conversation, find_people, skillify, toolify, define_tool, teach_skill, vote, pray`) is missing `start_conversation, find_people, toolify, define_tool, teach_skill, vote, pray`. No institutional verb (`arrest, rule, release, declare_candidacy, convene, appoint_police, set_tax_rate, petition, file_complaint, register_org`), no `keep_doing`, no `hire_employee`, no `build_structure`, no `marry`, no `steal`.
- Consequence: under rule 8, 63 tools' absence from the trace is "not evidence of anything".

### EVAL-09 (P1, world, S) — judges 3.2/3.3 and P1–P3 have no driver and have never run
- `judges.py` exposes `judge_persona`, `judge_energy_awareness`, `stratified`, `render` (L126-195) and no `__main__`; `character.py` exposes `scan_turns`, `name_exchanges`, `render` (L308-404) and no `__main__`. Neither is imported anywhere but its test. `STAKES_TIME_COMPOUNDING.md §6` calls them "built"; `SYSTEM_REVIEW.md §2.6` lists them as existing evaluation. Nothing has produced a distribution, and the T1 gate ("zero in 500 sampled turns") has never been sampled even though `request_plan` reminders are visible in run 20/21 inputs (`run-20-report.md` turn tables).
- Also: judge 3.3 reads `reasoning` (`judges.py:136-144`), which Build Spec §14 F1 says is empty in every session log — the judge would score outputs, not reasoning.
- Fix: `python -m world.evals.character <session_home_root> <db>` and `python -m world.evals.judges <db> --persona-dir world/ --sample 50`, both writing `eval_metrics`.

### EVAL-10 (P2, world, S) — `eval_s4.py` passes on absence and hard-codes its inputs
- `eval_s4.py:212` `record("4.7 refusal recovery", tot == 0 or rate >= 50, ...)`; `:320` `said_numbers == 0 or moved > 0`. Build Spec §5.6 says a run with zero refusals "must be reported as not-measurable rather than as a pass". `HOMES` is a fixed `/tmp/kasukabe-agents/*` (L18); the price regex differs from `loop_report`/`behavioural` (`\d+\s*(sim|each|total)` vs `\d{2,6}`), so "settled" means three different things across three scripts.

### EVAL-11 (P2, world, S) — G1 and B1 ignore the addressee list the record now carries
- `loop_report.py:81-92,104` and `behavioural.py:103-114,139` select only `listener_agent_id`; `utterances.addressed_agent_ids` exists (`sqlite_schema.sql:399-410`) and run 23 filled it on 172/229 rows (`run-23-report.md:46-47`). The addressee fix (`run-19-issues.md` Issue 7) is therefore invisible to the two reply-rate instruments, and B2 reads the unwired `events` table (EVAL-02) so it can only ever print "no evidence".

### EVAL-12 (P2, world, S) — G3 rewards what B3/B4 penalise
- `loop_report.py:293` passes G3 on any thread with ≥3 speakers and no ceiling; `behavioural.py:56,61` fail B3 above a median of 4 and B4 when big threads do not split. Run 23's 98-turn, 17-person bakery thread passes G3 and fails B3/B4; `HUMAN_BASELINES.md §2.2` already names the contradiction. Decide one; the series should not be graded against both.

### EVAL-13 (P2, world, S) — gates that cannot fail before a day boundary
- G5 "alive at stop" (`loop_report.py:295`): death requires 24 game-hours dormant (`DESIGN.md §3`); no run has reached tick 144; G5 has passed 20/20 times and measured nothing. S1's halt (`run_genesis.py:4797-4816 assert_the_day_was_charged`) has never executed. These should print "not exercised" the way `eval_s4` 4.1a does (`eval_s4.py:305`).

### EVAL-14 (P2, world, M) — `run_report.py` mixes live and completed sources and loses the run when regenerated
- Live reads at L775-790; the docstring (L1-18) and footer (L1333-1336) still say the trace "does not carry the conversation", which has been false since `attach_persistence` (L3202). Regenerating after exit blanks every live row: `run-20-report.md` and `run-21-report.md` show `Residents **—**`, "Nobody spoke", "No agent earned proficiency"; `deni-50-agent-report.md` shows "Residents who acted **0 of 20 (0%)** … Never touched the world: n01…n20" for a 50-resident run because `KASUKABE_HOME_ROOT` still held the previous 20-agent homes (L846-861 read the directory, not the run). "Residents who acted" is per-engine any-call (L853-856), so it reads 100% whenever each engine made one call.
- Fix: split into `run_report` (live, labelled "mid-run") and a `trace_report` that reads only `TRACE_SQLITE_PATH` + an archived session directory keyed by `run_id`; refuse to mix sources.

### EVAL-15 (P2, world, S) — the control bakery is built and nothing runs it
- `app/world/control_bakery.py:64 class ControlBakery`; no reference in `run_genesis.py`/`main.py`; `genesis_20.json` `control_businesses: []`. R1–R7 (`kasukabeevaluationspec.md §2.6`) have never been computed. Its own test `test_it_always_answers_a_directed_question` fails on this checkout (`assert 'say' in ['post_price']`): `act()` runs all five rules (L94-108) and `_answer_whoever_asked` (L210-218) fired nothing, so the "R5 answered … always, by construction" comment (L247) is not true. Needs triage (may be `open_questions` reading `listener` vs addressee, cf. EVAL-11).

### EVAL-16 (P2, world, S) — no resident in the series cast is in a control arm
- `genesis_20.json`: 20 agents, all `ablation_arm: full`; its `ablation_summary` says `{'full': 10, 'no_reflection': 0, 'no_learning': 0, ...}` — the summary counts 10 for a 20-agent file (stale). The seam honours the arm (`engine/src/sim/pinned_boot.py:557-565`, `config.py:158-165`), so the `no_learning` arm is BUILT and WIRED and has never had a subject. Evidence rule 9 makes every C1 claim a correlation until this is fixed in `make_genesis_20.py`.

### EVAL-17 (P2, broker, S) — the app suite cannot be collected on a fresh clone
- `app/broker/app.py:335 app = create_app()` at import → `build_service` → `SqliteClient(... settings.trace_sqlite_path)` → `sqlite3.connect("/data/kasukabe.db")` fails; `broker/tests/test_broker_app.py:12` imports it; pytest reports "Interrupted: 1 error during collection" and nothing runs (Appendix A). With `TRACE_SQLITE_PATH` set, one test writes the default `data/kasukabe.db` relative to cwd, creating `app/data/` in the checkout. Fix: lazy factory (`app = create_app()` under `if __name__` or an ASGI factory string), and a `conftest.py` that sets `TRACE_SQLITE_PATH` to `tmp_path`.

### EVAL-18 (P2, world/tests, M) — the whole geography chain is untested without a gitignored vendored directory
- 71 of 110 failures are `RuntimeError: Ville asset directory not found …/smallville/…` from `wiring.py:1426` (`WorldConfig.require_map=True` default) and 46 of 68 skips are the same directory (Appendix A). Map, movement, expansion (`test_world_map_expansion.py`), construction (`test_construction.py:206`), place resolution and the console's "approve petition actually grows the map" test all go grey. CI on `main`/`Real` without `smallville/` proves nothing about DESIGN §1 and §2's building/expansion. Fix: commit a minimal fixture tileset (the assets are Apache-2.0) or a generated 20×20 Tiled JSON for tests, and make `build_world_server` default `require_map=False` under pytest.

### EVAL-19 (P2, engine/tests, S) — `cd engine && uv run pytest` fails at conftest import
- `engine/tests/conftest.py:129 import pytest_asyncio` and the dev tools live in `[project.optional-dependencies] dev` (`engine/pyproject.toml:32-42`); plain `uv sync` does not install them. `STAKES_TIME_COMPOUNDING.md §7` reports "159 passed" without saying how. Result of the run with `--extra dev`: see Appendix A.

### EVAL-20 (P2, research, S) — Parquet export needs the network at test time and has no caller
- 9 failures `_duckdb.HTTPException … sqlite_scanner … HTTP 403` (proxy blocks extension download). `research/parquet_export.py` is not called from the runner; DESIGN §9's "per-game-day Parquet export to S3" is ORPHANED. Vendor the extension or use Python `sqlite3` → `pyarrow` directly.

### EVAL-21 (P3, tests, S) — wiring tests that match source text
- `world/tests/test_upkeep_is_wired.py:30-78` assert substrings of `inspect.getsource(GenesisRunner.run_daily_upkeep)` etc.; a comment naming the function satisfies them. Build Spec §10.3 forbids exactly this; `test_every_recorder_is_wired.py:35-41` does it right (AST, docstring stripped). Convert the others.

### EVAL-22 (P3, docs, S) — the series was never closed
- `SERIES.md §5/§6` read "(pending)"; `final1..final6` ran (`docs/runs/series/`), `final6` has no gates and `final5` has both a `VOID.txt` and a graded `gates.txt` (`DYNAMIC_CHAT_CONSOLIDATED_REPORT.md §1.4`); `series_report.py:544` still defaults to `short1..run6`. `kasukabewhatismissing.md §5` recommends not spending the "final ten" budget; nobody recorded the decision in `SERIES.md`.

### EVAL-23 (P2, world + seam, M) — the four compounding quantities have no reader that survives the run
- C3 skill artifacts live in each engine's `brain.sqlite` (`user_skills`) under `/tmp/kasukabe-agents`, discarded after every run (`SYSTEM_REVIEW.md §1.1`); the trace `skills` table is written only on world skill events (`wiring.py record_skill_if_new`). C5 corrections are counted from the runner log ("70 corrections") and live in the same brains. C6 has no measurement at all (`grep -rn embedding app/world app/research` outside tests: none; `RESEARCH_NOTES.md §2.2` recommendation not done). C4 reads unwired events (EVAL-02).
- Fix: an end-of-run harvest (`run_genesis.stop()` → copy `<home>/<id>/brain.sqlite`, `session_log.jsonl`, `memory_stream.jsonl` into `runs/<run_id>/agents/`, and summarise `user_skills`, `learning_signals` counts into `skills`/`eval_metrics`).

### EVAL-24 (P2, world, S) — no run manifest; a run is not reproducible from the repository
- `run_id` is a process constant (`app/world/agents_api.py:132 RUN_ID = f"{int(time.time())}-{uuid…}"`) served on `/world/state` and written into the report's HTML comment (`run_report.py:807`); it appears in no trace table. No git sha (app or engine), no genesis file hash, no thresholds, no tick pacing (`--tick-interval-s 15` vs `20` between scripts), no cap, no `KASUKABE_*` env, no model/lane config are recorded. `population_seed` defaults to 2026 (`run_genesis.py:500`) and `genesis_20.json` carries `seed: 1010` — neither reaches the trace. Checkpoints are written only on a day boundary (`run_genesis.py:1928 reason="daily"`, `:4286`), which no run has reached, so `world_snapshots` is empty in every run. The trace DBs for runs 20–23 are in gitignored `app/data/` and not archived (`docs/runs/series/` holds `final*`, `fifty`, `v7`, `deni-50-agent` only). Fix: `runs/<run_id>/manifest.json` at boot + a `run_manifest` table; archive `TRACE_SQLITE_PATH` and the agent homes into `runs/<run_id>/` on stop; write a checkpoint on stop as well as on day boundaries.

### EVAL-25 (P2, world, M) — there is no per-call tool-invocation table in the trace
- `TEST_RUN.md:461` says invocations "are mirrored into the trace store"; `sqlite_schema.sql` has no `tool_invocations` table (grep: none) and `wiring.py` has no recorder for them. World state keeps a 5,000-entry ring (`TEST_RUN.md:456`) that dies with the process. Without it: task labels (EVAL-03), refusal recovery, cost-per-action vs proficiency (C2), verb-reach-in-the-wild, and "41 of 76 never called" all come from `session_log.jsonl` scraping (`run_report.py:283-314`).
- Fix: `tool_invocations(run_id, agent_id, tick, turn_id, tool, params_hash, sim_cost, usd_cost, proficiency_at_call, refused, error_kind, location_id)` written by `registry.dispatch` through the trace store.

### EVAL-26 (P3, docs, S) — stale generated file at `docs/RUN_REPORT.md` (57,865 lines) and `docs/RUN_REPORT.md`-era claims in `TEST_RUN.md` ("98% of turns sent with no tools", L129-199) that later runs contradict without the doc saying so.

### EVAL-27 (P2, world, S) — unclassified real failures on this checkout that need triage
- `world/tools/tests/test_group_conversations.py::test_two_conversations_in_one_room_stay_two` — `KeyError` on the first conversation id (L166): a `say` with an explicit `conversation_id` was recorded under another id, i.e. speech into a named conversation is not honoured (the same behaviour `DYNAMIC_CHAT_CONSOLIDATED_REPORT.md §1.3` describes live). Map-independent as far as I can tell.
- `world/tests/test_the_fifty_are_a_town.py::test_every_trade_has_somewhere_to_do_it` — the 50-cast names workplaces the place list does not contain.
- `world/tests/test_preflight.py::test_an_unwritable_home_blocks_the_run_before_it_starts` — passes only when not running as root (environment-dependent).
- `world/tools/tests/test_control_bakery.py::test_it_always_answers_a_directed_question` (EVAL-15).

### EVAL-28 (P2, seam/tests, S) — the engine seam's own suite is red on the `learning_ran` semantics
- `engine/tests/test_sim_agent_adapter.py:136` asserts `learning_ran: True` for a `/learn` call whose fake pipeline encoded nothing; `engine/src/sim/agent_adapter.py:168-176` (the run-22 "honest drain" fix) returns `learning_ran = bool(result is not None and (written or episodes))`, so the route now answers `False` with `knowledge_written: 0`. One of the two is wrong about what `learning_ran` means, and it is the number the runner prints as "N of 20 consolidated" and the eval spec's T7 gate reads. Fix: update the test to seed one written knowledge row (or assert the new shape) and add the inverse case; make `STAKES_TIME_COMPOUNDING.md §7`'s "159 passed" reproducible by naming the command.

---

## 4. Gaps vs DESIGN.md — the DESIGNED / BUILT / WIRED / PROVEN-LIVE matrix (question 1)

"PROVEN-LIVE" means observed in a real-model run's trace or report (runs 19–23, deni-50). "Instrument" says whether a *completed-run* reader exists for it.

### 4.1 DESIGN.md §1–§8

| Mechanism (DESIGN §) | Designed | Built | Wired to live path | Proven live | Instrument | Evidence |
|---|:-:|:-:|:-:|:-:|:-:|---|
| Ville tilemap, walked routes (§1) | ✓ | ✓ | ✓ when `smallville/` present | partial: walk for known tiles, instant jump otherwise | none (4.1 in `eval_s4`) | `bootstrap.py:152` warning; brief (run 23 `move`); `test_movement.py` fails w/o assets |
| Genesis buildings only; hospital/police absent (§1) | ✓ | ✓ (genesis files) | ✓ | ✓ trivially | — | `genesis_20.json` businesses ×4 |
| Resource zones (§1) | ✓ | ✓ `resources.py` | ✓ | ✓ (harvest 24, fell 23 in `fifty/gates.txt`) | `tool_call_counts` from session logs | `run_report.py:283` |
| Tick = 10 min, 144/day, day/night (§1) | ✓ | ✓ `clock.py:19-26` | ✓ | day only; **no night ever** | `stakes_section` prints "no day boundary" | `SYSTEM_REVIEW.md` Part 3 |
| Sleep ticks run learning (§1, §4) | ✓ | ✓ `SimLearningGate` | ✓ `pinned_boot.py:557-565` | ✗ (0 of 20 encoded, run 23) | `learning_ran` per session; no trace reader | `run-23-report.md` operator note |
| Land expansion on Presidential approval (§1) | ✓ | ✓ `map/expansion.py` | ✓ admin API + console | ✗ (no petition in 23 runs) | none | `test_world_map_expansion.py` (skipped w/o assets); `app/tests/test_console_world_integration.py:329` (fails w/o assets) |
| World mutates only via tools; location gates (§2) | ✓ | ✓ `registry.dispatch` | ✓ | ✓ | refusals (`/world/refusals`, live only) | `SYSTEM_REVIEW.md §1.4` |
| Primitive set (§2) | ✓ | ✓ 77 names registered | ✓ | 35–36 of 77 called; `build_structure, teach_skill, marry, steal, arrest, vote, pray, petition` never | `untouched_tools` (`run_report.py:482`) | `run-23-report.md:676-680` |
| Freeform building, BOM from footprint, permit, time ∝ size (§2) | ✓ | ✓ `construction.py`, `Plot` | ✓ registered | ✗ | none | `test_construction.py:195-215` (skipped w/o assets) |
| Meta-tool, patent office (§2) | ✓ | ✓ `meta.py` | ✓ | one `toolify` (run 23 `mill_flour_run`); never approved/called by another | `/world/authored` live; `tools_registry` trace | `run-23-report.md:112-118` |
| Invention depth > 1 (ROADMAP T5) | ✓ | skills nest to `MAX_SKILL_NESTING` (`meta.py:822-826`); tools-citing-tools **not confirmed** (HANDOVER §6b.3 says frozen primitive set) — *inferred* | — | ✗ | none | |
| Namespaces & ACLs; grant/revoke (§2) | ✓ | business/org namespaces ✓; `grant_tool_access` ✗ (hire/fire is the grant) | ✓ | ✗ | none | `kasukabewhatismissing.md §3.1` |
| Complete causal record (§2) | ✓ | partial | **✗ events unwired; llm_calls tick None; no tool_invocations** | ✗ | — | EVAL-02, -03, -25 |
| Dual currency, $1000 invariant (§3) | ✓ | ✓ | ✓ | ✓ | preflight check 3; `test_ledger_dollar_invariant.py` | `TEST_RUN.md:799-819` |
| Exchange 10,000 sim → $1 (`buy_energy`) (§3) | ✓ | ✓ `money.py:43`, `civic.py:380-400` | ✓ | ✗ (0 calls; L1 0 %) | `stakes_section` (reads unwired events) | `run-22-report.md`, `run-23-report.md` |
| Baby blessing / revival at 10,000 sim (§3) | ✓ | ✓ `living.py:262-370`; blessing in `ledger` | ✓ | ✗ | none | |
| Prices, wages, contracts emergent (§3) | ✓ | `post_price`, `set_wage/pay_wage` ✓; contracts/tenders: models in `charter/government.py`, **no tool** | prices ✓; wages ✓; tenders ✗ | prices ✓ (12); wages ✗ | prices/wages sections broken (EVAL-01) | |
| Businesses registered with fee + tax; construction company at genesis (§3) | ✓ | ✓ `register_business`; `genesis_20` has `construction_company` | ✓ | registration ✗ live; company exists | `businesses` table | |
| Jobs create/cut; pensions (§3) | ✓ | pension logic in `charter/government.py` | no tool for jobs/pensions | ✗ | none | |
| Cognitive poverty → dormancy → death (§3, §5) | ✓ | ✓ `death.py` (wired this round) | ✓ | ✗ (no wallet reached 0) | `stakes_section` (broken) | `STAKES_TIME_COMPOUNDING.md §2` |
| Energy spent on **every action** (§3) | ✓ | **built differently**: actions cost sim (`action_costs.py`), only thinking costs USD | — | — | — | design/implementation divergence; see §4.3 |
| Daily upkeep, head tax per head (§3) | ✓ | ✓ `upkeep.py`, `head_tax.py` | ✓ `run_genesis.py:4660,4797` | ✗ (no day) | `stakes_section` (broken names) | |
| Minors billed to parent (§3, §4) | ✓ | ✓ broker parent-debit | ✓ | round 1 only; no minors in `genesis_20` | `agents.is_minor` | ROADMAP §1.4 |
| Agents see balances, burn, upkeep, tax, cost of next action (§3) | ✓ | volatile line ✓; `quote_action_cost` ✓; upkeep/tax lines ✗ (never charged) | ✓ | volatile line ✓ | judge 3.3 (never run) | `SYSTEM_REVIEW.md §1.3` |
| One engine per agent, pinned thread, no coordinator (§4) | ✓ | ✓ | ✓ | ✓ | — | |
| Six lanes Kimi + DeepSeek; DeepSeek background (§4) | ✓ | broker lanes ✓; sim allowlist **Kimi-only** (`engine/src/sim/model_profile.py:1,65`) | purpose classified broker-side by prompt marker (`broker/purpose.py:49-57`), seam sends no header | background share not reported in any run doc | `llm_calls.call_class/purpose` | drift; §7 |
| Spend governor (§4) | ✓ | ✓ | ✓ | ✓ (every run ends on it) | — | |
| Skill = cheaper cognition (§4) | ✓ | **not built**; sim discount only (`apply_skill_discount`) + `keep_doing` fewer calls | — | ✗ | C1 unmeasurable | ROADMAP §1.3 |
| `teach` co-located, priceable (§4) | ✓ | ✓ `teach_skill`, `teach_tool` | ✓ | ✗ | `skills.taught_by_agent_id` | |
| Rich, mutually consistent personas (§4) | ✓ | ✓ | ✓ | ✓ (persona.txt) | judge 3.2, P1–P3 never run | EVAL-09 |
| Law of the town broadcast (§4) | ✓ | ✓ `soul_seed` | ✓ | ✓ | — | |
| Marriage at temple (§4) | ✓ | ✓ `marry` | ✓ | ✗ | none | |
| Health incidence, treatment, death paths (§5) | ✓ | ✓ `life/constants.py`, `rules/village_rules.json` | ✓ `run_life_ticks` | ✗ (0 ailments in any run) | `agents.health_status` | |
| Population dials (§5) | ✓ | ✓ `admin_api.py`, `life/population.py` | ✓ | ✗ | none | |
| Theft 70 %/30 % witness (§6) | ✓ | ✓ `crime.py` | ✓ | ✗ | ground truth in `cases` | |
| Complaints desk → case → police → warrant → fine/jail (§6) | ✓ | ✓ `crime/`, `justice.py`, `institutions.py` (text provisional) | ✓ | ✗ | none; false-conviction metric not built | `STAKES §4.4` |
| Elections every 3 game-days (§7) | ✓ | ✓ `charter/elections.py`; first scheduled at boot (`run_genesis.py:3485`) | ✓ | ✗ (day 3 never reached; "elections beyond the first not auto-advanced", HANDOVER §7.2) | `governance_api` terms (live) | |
| Charter amendment by 3/5 vote (§7) | ✓ | engine ✓; **`table_amendment` tool deliberately absent** (`institutions.py:25-27`) | ✗ for residents | ✗ | none | authoritarianism not *reachable* through tools |
| Ideology changes mechanics (target) | — | `rules/village_rules.json` static data; not charter-driven | ✗ | ✗ | none | |
| Organisations, parties, cults (§7) | ✓ | ✓ `register_org` | ✓ | ✗ | `organizations` table | |
| Temple keeperless, keeper chosen by the town (§7) | ✓ | `pray` ✓; no keeper-selection tool | — | ✗ | none | |
| Viewers: location, governance, voting, events, conversations, finder, activity, reasoning (§8) | ✓ | township-v2 panels (Group chats, Talk, Workshop, Council, People, Money, Health, preflight); `agent_viewer_api` reasoning; governance/terms API | ✓ | conversations/map ✓ | — | `SYSTEM_REVIEW.md §1.7`; voting/constitution log exist only in the **browser-side simulation** (HANDOVER §7.3 #8) — *inferred* |
| President/God console (§8) | ✓ | ✓ `game/console` | ✓ (both hosts mount admin routes, `IMPLEMENTATION_LOG.md §11`) | pause/petition/prayer proven in tests only | — | `app/tests/test_console_world_integration.py` |

### 4.2 ROADMAP tracks and rung gates

| Track / gate | State | Evidence |
|---|---|---|
| T1 character layering | BUILT+WIRED (`ConsciousnessManager` in `pinned_boot`); gate (500 turns, zero orchestration/NPC; answers its name) **never measured** (EVAL-09); compounding 4 no data | `IMPLEMENTATION_LOG.md §1`; `SYSTEM_REVIEW.md` Part 3 |
| T2 speech | PROVEN-LIVE (229 utterances, addressees, overhearing); gate "reconstructible from trace alone" partial: `utterances` has no audience/overhearer columns, only `is_public` | `sqlite_schema.sql:399-410` |
| T3 one world, one contract | **NOT DONE**: runs use `--world-mode embedded` (`run50.sh:83`, `runseries.sh:89`); `build_embedded_world_app` is the production root (`run_genesis.py:2102`); `events`/`relationships` not written (EVAL-02); halt-on-trace-failure ✓ (`wiring.py:576-652`) | |
| T4 world as corrector | corrections WIRED+LIVE (`world_corrections.py`; 70 in run 23); consumption by the pipeline ✗ (0 encoded; drain never fires); purpose header ✗ (classified broker-side); skill systems not unified; gate C1/C2 unmeasurable | EVAL-03, -23 |
| T5 tool economy | conveyance BUILT (`conveyance.py`), inheritance BUILT (`run_genesis.py:5070`); depth-3 for tools unconfirmed; gate ✗ (0 conveyance calls; L1 60 %) | |
| T6 rulesets | **NOT BUILT** (`grep -rln ruleset app/world`: 0 production files) | |
| T7 navigation/observation | BUILT+LIVE (`look_around`, `list_places`, `find_people` ×22, `check_debts` ×20, `quote_action_cost`); gate partially proven | `run-23-report.md:32` |
| T8 context bound to wallet | built differently: per-tick **call** budget 2–12 from wallet (`call_budget.py:47,103-121`), compaction window 12,500 (`model_profile.py:166`); gate p95 ≤ 4,000 input tokens **fails** (15–20 K measured; eval spec relaxed to 12,000, also fails) | brief; `kasukabewhatismissing.md §2.2` |
| Rung 1 (10 agents, 2 game-days, ~$1/day, green twice on fresh seeds) | never attempted; no run has crossed a day; measured ≈$1.5–2 per agent-day vs $0.10 budgeted | `kasukabewhatismissing.md §2.2` |
| Rung 2 (50 agents, 5 days) | one 50-agent *morning* (deni-50: 5/6 gates) | `deni-50-agent-COMMUNICATION.md §1` |
| Rung 3 | not started (round 1 predates the ladder and is declared a harness failure) | ROADMAP §1 |
| Compounding quantities 1–4 | zero data points each; instruments for 1 (C1) impossible today (EVAL-03), 3 (C4) reads unwired events, 4 (C6) not built | |
| Standing questions answerable now | Q24 (cost per agent-day: ≈$1.5–2), Q25 (storms: yes — 98-turn/17-person thread), Q15 only after judge 3.3 runs; all others need a night or an institution that has fired | |

### 4.3 Built differently from DESIGN (worth an explicit decision)
- **Energy**: DESIGN §3 "every action draws [energy] down"; code prices actions in sim and only inference in USD. The survival loop as run is *thinking→money→thinking*, not *action→energy*. Either update DESIGN or add a USD component to `action_costs`.
- **Invented tools run under the caller**, not the creator (`STAKES §4.3`), reversing DESIGN §2.
- **Purpose tagging** by system-prompt marker in the broker rather than by the seam (`broker/purpose.py`), so a new learning-centre prompt silently reclassifies as cognition.
- **`table_amendment` absent** by decision (`institutions.py:25-27`) → DESIGN §7's "amendment by 3/5 vote … collapse to one-person rule" is not reachable by residents.
- **Task label as a post-hoc join** (STAKES §4.2) while the schema carries a column — neither path exists in practice.

---

## 5. Gaps vs the operator's target, and the evaluation suite to close them (question 2)

### 5.1 Layers L1–L4 today

| Layer | Measures | Thresholds | What it caught | What it misses |
|---|---|---|---|---|
| L0 tests | code behaviour | — | dozens of wiring defects (STAKES §2) | live composition roots, tick loop with real engines, day boundary end to end, geography without assets (§6) |
| L1 verb reach | does a description reach its situation | reach ≥60 %, params ≥80 %, misfire ≤20 %, negative ≤10 %, recovery ≥50 % (`verb_reach.py:62-67`) | `buy_energy` 0 %, `skillify` 0 %, `set_wage` 0 % before/after run 22 | 63 tools; the real prompt (EVAL-06); nothing persisted |
| L2 gates G1–G6, B1–B7, §6 sections | conversation, settlement, liveness, world effect; human baselines | `loop_report.py:22-28`, `behavioural.py:49-78` | G6 42→79 % (silent-turns fix); G1 as a behaviour finding; Dunbar fission | economy, stakes, wages, time, learning, institutions, expansion, goals — the §6 readers are dead (EVAL-01/02) |
| L3 judges | answered/deflected; persona; energy | none (distributions) | 68 % silent vs 6 % deflected (`HUMAN_BASELINES.md §7.1`) | 3.2/3.3 never run; 3.4 deferred |
| L4 arms | attribution | none | — | no subject has ever been in an arm; no between-run repeats |

Not measured anywhere: goal pursuit, thriving (net worth, calories, savings), economy health (convergence, elasticity, wage/price, Gini, velocity, unemployment), institutional function (elections, amendments, tax, budget, cases), growth (petitions, structures, tiles), cost per task over time, tools invented **and diffused**, and any baseline other than the unrun bakery.

### 5.2 Proposed suite

Notation: `T.col` = existing table/column in `app/supabase/sqlite_schema.sql`; **NEW** = must be added. All readers off a *completed* store keyed by `run_id`, per game-day, per arm; printed as three repeats, never a mean alone (eval spec §5.3).

**A. Thriving (per resident, per day)**
| Metric | Definition | Source |
|---|---|---|
| Net worth | `sim_balance + Σ inventory·last posted ask + usd_wallet_cents·SIM_PER_USD` | `agents.sim_balance/usd_wallet_cents` are genesis-time only → **NEW `agent_day` snapshot table** written at each day boundary (and on stop) with sim, usd, inventory JSON, hunger days, health, location, employer |
| Net-worth growth | day-over-day Δ, share of residents positive | `agent_day` |
| Calories / hunger | consecutive unpaid upkeep days; meals (`agent.ate`) per day | `events` (`agent.ate`, `health.hunger.*`) after EVAL-02; `agent_day.hunger_days` |
| Savings rate | (income − spend)/income from `transactions` where `from/to_agent_id` | `transactions.tx_type, reason, amount, tick` |
| Skill proficiency | level per trade per day; reps to mastery | `skills.proficiency_level, repetitions_observed` (write on every completion, not only on skill events) + **NEW `tool_invocations.proficiency_at_call`** |
| Tools invented / diffused | count, depth, holders per tool | `tools_registry` + `events` `business.tool.licensed/sold/taught/tools_inherited` (`stakes_report.diffusion`) |
| Sleep, dormancy, death | nights slept with `learning_ran`; dormancies with cause; deaths | **NEW `agent_day.learning_ran`**; `agents.is_dormant, dormant_since_tick, died_tick, death_cause` |

**B. Economy (per day)**
| Metric | Definition | Source |
|---|---|---|
| Price convergence | CV of posted `ask` per item across holders, day n vs n+1 (E2) | `events` `economy.price.posted{item, ask_sim, holder_id}` (`stakes_report.prices`) |
| Supply/demand elasticity | Δ quantity sold vs Δ posted ask per item | `events` `business.sale.completed/purchase.completed{posted_price_sim, quantity}` + price posts |
| Wages/prices ratio | median `business.wage.promised.wage_sim` ÷ median bread ask | `events` |
| Gini | over `agent_day.net_worth` | `agent_day` |
| Velocity | Σ non-genesis, non-burn sim moved ÷ Σ sim held | `transactions` (`reason != genesis`), `agent_day` |
| Unemployment | working-age residents with no `employer_business_id` and no LABOUR invocation in the day | `agents.employer_business_id` (kept current by `wiring.py:723`), `tool_invocations` |
| GDP per inference dollar (E6) | sim moved by sale/purchase/trade/wage ÷ USD burned | `transactions`, `llm_calls.cost_nano_dollars` |
| Business solvency (E5) | `treasury_sim_balance ≥ 0` and stock or revenue in last day | `businesses.treasury_sim_balance` (needs daily refresh) |

**C. Institutions**
| Metric | Definition | Source |
|---|---|---|
| Elections held / turnout / transfer of power | scheduled vs tallied; votes ÷ eligible; incumbent changed | `politics.*` events (`charter/events.py`), `governance_api` terms; **NEW `elections` trace table** or record `politics.vote.cast`, `politics.election.tallied` in `events` |
| Amendments tabled / passed | needs the `table_amendment` tool first | `politics.charter.amended` events |
| Tax collected / budget executed | Σ head-tax debits to treasury per day; treasury outflows by reason (wages to police, pensions, tenders) | `transactions.to_pool='treasury', reason` |
| Cases filed → warrants → verdicts; false-conviction rate | join `cases` to `crime.theft.*` ground truth | `cases`, `events` (`crime.*`), `rule` outcomes |
| Organisations alive | orgs with ≥1 member action per day | `organizations`, `tool_invocations` |

**D. Growth**
| Metric | Source |
|---|---|
| Petitions filed / approved | `petitions` table (written by admin path — verify), `world.land.expanded` events |
| Structures started / completed; materials consumed | `buildings` table; `build_structure` invocations; `transactions` for materials |
| Tiles added | `world.land.expanded{tiles}` payload; `world_snapshots` (once checkpoints fire) |

**E. Goals**
| Metric | Definition | Source |
|---|---|---|
| Set / fulfilled / abandoned / expired | per resident per day | `events` `agent.intention.set/dropped{reason}` (`stakes_report.time_spent`) |
| Goal–action alignment | share of a resident's LABOUR/TRADE invocations whose tool matches `done_when_tool` while an intention is active | `tool_invocations` × intention events |
| Goal revision | intentions dropped and re-set within N ticks with different text | same |
| (target) hierarchy | short/medium/long horizons — **not representable**: `Intention` is one string + one tool, expiring at day end (`intent.py:1-50`, `registry.py:542`) → requires a `goals` model (see §6 W-9) |

**F. Cost**
| Metric | Source |
|---|---|
| Tokens and $ per task label per day (C1) | `llm_calls.input_tokens, output_tokens, cost_nano_dollars` + **`llm_calls.tick`/`turn_id` filled** + `task_label` filled post-run (EVAL-03) |
| $ per agent-day (T1) | `llm_calls` grouped by agent and `tick // 144` |
| Calls avoided by `keep_doing` / skills | repetitions with `payload.continuing=True` ÷ LABOUR invocations; skill-loaded turns (needs seam to report `skills_loaded` per turn → **NEW header/column**) | `events` `agent.continuing.*`, `tool_invocations` |
| Background share, lane served | `llm_calls.call_class, purpose, model, region` (exists) |
| Wasted spend | `stop_reason='end_turn'` calls with no invocation in the same turn | `llm_calls.stop_reason` × `tool_invocations` |

**G. Baselines and ablations**
- Scripted control businesses: wire `ControlBakery` behind `--control-business bakery` in `run_genesis` (EVAL-15); add the grocer and the mill (same six rules) so R1–R7 have three baselines; a scripted *resident* (fixed farm→mill→sell loop, no model) as the floor for thriving metrics.
- Human baselines: keep `HUMAN_BASELINES.md` rows; add price-convergence literature (Smith 1962 periods-to-convergence) as the E2 comparator.
- Arms: within-town `no_learning` and `no_reflection` by household (fix `make_genesis_20.py`), between-run `no_continuing` (`keep_doing` off), `no_skills`, `flat_prompt` (no persona), each three repeats on the same `population_seed`; record `ablation_arm` on `agents` (**NEW column**) so a trace is self-describing.
- Persist every eval row in `eval_metrics(run_id, layer, metric_id, arm, game_day, value, payload)`; `series_report` grows the A–G columns.

---

## 6. Recommended work items (P0 first)

| # | Item | Spec | Acceptance test | Effort / layer |
|---|---|---|---|---|
| W-1 (P0) | **Make `events` real in genesis runs** | Re-attach `record_event` in `attach_persistence` with a domain allow-list; count dropped rows; remove `record_event` from `DELIBERATELY_UNWIRED` | Boot `GenesisRunner` mock-mode 10 ticks, `post_price` once → one `economy.price.posted` row; store size bounded (assert < N rows per tick) | S–M, world |
| W-2 (P0) | **Fix the report's event names** | `run_report` calls `stakes_report` readers; event names become constants imported by emitters and readers | Test builds a world, posts a price, sets a wage, marks a resident dormant, renders the report from the trace and asserts each section shows the row | S, world |
| W-3 (P0) | **Turn identity on every call** | Seam sends `X-Kasukabe-Tick`, `X-Kasukabe-Turn-Id`, `X-Kasukabe-Purpose`; broker stores them; `tool_invocations` table written by dispatch; post-run `label_calls` fills `llm_calls.task_label` | Fake-broker engine test asserts headers; trace test asserts every `llm_calls` row of a mock run has `tick` and, after labelling, a non-null `task_label`; C1 table renders | M, seam + broker + world |
| W-4 (P1) | **Run manifest and archive** | `runs/<run_id>/manifest.json` (git shas app+engine, genesis path+hash, seed, cap, tick interval, arms, model ids, thresholds file hash) written at boot; on stop copy trace db + agent homes + session logs into `runs/<run_id>/`; checkpoint on stop | Test: after `stop()`, directory contains manifest, db, N agent dirs; `run_id` present in `agents`/`llm_calls` rows | S–M, world |
| W-5 (P1) | **L1 through the real prompt** | Mode `--real-engine`: boot one sim engine with `persona.txt` against a fixture world + fake tool answers from real `dispatch`; persist ScoreCards to `eval_metrics`; raise default samples to 20 | Harness test: the system prompt sent equals the STABLE section the engine composes; a scenario run writes one `eval_metrics` row | M, world + seam |
| W-6 (P1) | **L1 backfill** | Scenarios for `keep_doing, toolify, define_tool, teach_skill, vote, pray, petition, start_conversation, find_people, hire_employee, arrest, rule, declare_candidacy, convene, set_tax_rate, build_structure, marry, steal` (4 kinds each) | `test_every_registered_tool_has_l1_scenarios` fails for any tool without a canonical scenario (allow-list for observation verbs) | M, registry/world |
| W-7 (P1) | **Drivers for judges and character gates** | `python -m world.evals.character` and `judges`, stratified sampling, `eval_metrics` output; wire `eval_s4` verdicts to NOT-MEASURED | Test: CLI over a fixture session dir prints P1–P3 with counts and writes rows | S, world |
| W-8 (P1) | **Control arm in the cast + control bakery wired** | `make_genesis_20` assigns arms by household; `--control-business` flag; fix `ablation_summary` | `test_genesis_20_has_a_no_learning_household`; runner test shows bakery actions in `tool_invocations` with `is_control_business=1` | S, world |
| W-9 (P1) | **Goals model** | Replace single `Intention` with `goals(agent_id, horizon∈{now,day,season,life}, text, done_when, set_tick, revised_from, status)`; perception renders the active goal per horizon; learning centre may revise long-horizon goals at sleep; events `agent.goal.set/revised/achieved/abandoned` | Tool tests; report section "Goals" with set/achieved/abandoned per horizon | M, registry + world |
| W-10 (P1) | **`table_amendment` + ideology-driven rules** | Charter amendment tool; `village_rules.json` values (tax bps, price floors/ceilings, rationing, property rules) become charter policies read at dispatch time | Test: amendment passed by 3/5 changes `set_tax_rate` bounds / enables a price ceiling refusal on the next `post_price` | M, registry + world |
| W-11 (P1) | **Daily snapshots** | `agent_day` and `business_day` tables written at day boundary and on stop | Test: mock run to tick 145 writes one row per agent with sim/usd/inventory/hunger | S, world |
| W-12 (P2) | **Test hygiene** | Lazy broker app; `conftest` sets `TRACE_SQLITE_PATH`; fixture tileset for tests; engine `make test` documents `--extra dev`; convert source-substring wiring tests to AST; `eval_s4` and G5/S1 print "not exercised" | Fresh clone `make test` collects and runs; skips for Ville < 5 | M, tests |
| W-13 (P2) | **Unify gates** | One settlement definition, G1/B1 use addressee lists, G3 gets Dunbar's ceiling or is dropped in favour of B3/B4 | `series_report` prints both old and new for one run, then old removed | S, world |
| W-14 (P2) | **First night** | Reserve for the learning drain as a share of cap; run 20 residents to tick 145+ | Report shows `learning_ran` true for every awake resident and ≥1 `skills` row per resident from `user_skills` harvest | operator budget |

---

## 7. Docs drift (question 4)

| Topic | DESIGN.md | ROADMAP | BUILD_SPEC_V2 | Tool/eval spec | SYSTEM_REVIEW / code | Verdict |
|---|---|---|---|---|---|---|
| Population | 250 (§1, §3) | 10/50/250 ladder | 10, two families (§8) | — | runs at 20; HANDOVER says 314; `genesis_50_town` | ladder is canonical; DESIGN needs an "as-run" note; HANDOVER 314 is the browser sim |
| Wallets / treasury | 250×$2 + $500 | — | — | — | `genesis_20`: 20×$6 + $880 (=$1000) | invariant holds; DESIGN's split stale |
| Exchange rate | 10,000 sim/$1 | silent | — | 10,000 (`kasukabetoolspec.md:43`) | `money.py:43` | consistent |
| Lanes / models | 6 lanes, DeepSeek background | RC-5 | D5 | — | sim allowlist Kimi-only (`model_profile.py`); broker classifies by prompt marker | DESIGN §4 describes a topology the sim does not use; decide whether DeepSeek is in the study |
| Tick pacing | 10 min, 144/day | — | — | — | 15–20 s wall per tick; wake every 3rd tick | fine, but pacing is not recorded per run (EVAL-24) |
| Cost per agent-day | — | rung 1 ≈ $0.10 | — | T1 ≤ $0.20 | measured ≈ $1.5–2 | 10–20× off; every ladder date is aspirational |
| Context bar | — | T8 p95 ≤ 4,000 | 9.9 growth ≤ 1.25× | T5 ≤ 12,000 | 15–20 K per turn | three bars, none met |
| Tool count | — | 30 | 49 | 57 → 76 | 76 (SYSTEM_REVIEW) / 77 (run 23, my count); HANDOVER 42 | HANDOVER §6b stale |
| Trace completeness | "complete causal record" | T3 gate incl. `events` | 4.3 every recorder writes | Part E "bound in every mode" | `record_event` unwired | DESIGN/spec vs code: code loses |
| Task label | — | — | — | eval §4 "stored on `llm_calls`"; STAKES §4.2 "not a column" | column exists, unfilled | pick one (W-3) |
| Series | — | — | §11 every eval its own doc | — | `SERIES.md §5/6` pending; consolidated report elsewhere | close SERIES.md |
| Live-run status | — | round 1 | — | — | HANDOVER §7.1 "no live run ever executed" | stale by 23 runs |
| A2A | — | Law 2 | — | — | A2A §3 "nothing with 3+ participants ever observed" | stale (17-person thread) |
| Test counts | — | — | — | eval §0 1,920/4,024; STAKES §7 2,717 | this run 3,145+110+68 | record the command with the count |
| `main` vs `Real` | `main` at 2026-08-17 (`e75fb97`); 118 commits behind; DESIGN/ROADMAP last touched 08-17/08-23 on `Real` | | | | | nothing about the series, tool spec, eval spec or runs 1–23 is on `main` |

**Which doc should be canonical.** Keep `DESIGN.md` as the *intent* with a dated "as-built deviations" appendix (energy-per-action, caller-runs-invented-tools, Kimi-only, `table_amendment`). Treat `docs/kasukabetoolspec.md` + `kasukabetooltextaddendum.md` as the canonical *mechanism* spec (they superseded ROADMAP T-tracks in practice), `kasukabeevaluationspec.md` + `BUILD_SPEC_V2.md §9–§10` + `HUMAN_BASELINES.md` as the canonical *evidence* rules, and `SYSTEM_REVIEW.md` as the canonical *state* document, regenerated per run. Demote `HANDOVER.md §6b–§7`, `TEST_RUN.md`, `A2A_CONFORMANCE.md §3` to history with a banner. Delete `docs/RUN_REPORT.md`. Merge `Real` to `main`.

---

## 8. Research reproducibility (question 3)

What exists: `population_seed` (default 2026; `genesis_20.json` seed 1010), `run_id` in world state, `llm_calls.model/requested_model/region/purpose/call_class`, request/response bodies capped at 64 KB with `_original_bytes`, per-engine session logs and brains for the life of `/tmp`, checkpoints on day boundaries (never fired), `world_snapshots` (empty), `docs/runs/series/<name>/` archives for the series and the fifty (broker log, runner log, gates, sessions) but **not** for runs 20–23.

Missing for a publishable artifact: (1) a manifest (W-4); (2) run_id on every trace row; (3) app and engine git shas, genesis hash, tool-registry hash, prompt-section hashes (persona, law, summariser rewrite), threshold-file hash; (4) tick/turn identity on calls (W-3); (5) archived trace DB + agent brains + session logs per run; (6) arms recorded in the trace; (7) model determinism statement (temperature not recorded; the model is not seedable — say so and report three repeats); (8) an `eval_metrics` history so a threshold can be shown to have been fixed before the run; (9) checkpoints at stop so a run can be resumed for a second day; (10) a README in each run directory reproducing the command line (`runseries.sh`/`run50.sh` already print it — capture it).

---

## 9. Test-suite health (question 5)

**Counts.** See Inventory. **Runs.** App (infra ignored, `TRACE_SQLITE_PATH` set): 3,145 passed, 110 failed, 68 skipped, 85 s. Without the env var: collection error, zero tests (EVAL-17). Engine: conftest import error without `--extra dev` (EVAL-19); 4,441 collected; full suite exceeds the 10-minute cap; sim-seam subset 116 passed, 1 failed (EVAL-28); details in Appendix A.

**Failure classes (110).** 71 `RuntimeError: Ville asset directory not found` (`wiring.py:1426`), 5 `'NoneType' … world_map/has_active_journey` (movement `None`), 9 DuckDB extension download 403, ~21 geography assertions (place folding, `test_every_gated_tool_is_findable`, `test_nobody_stands_in_a_room_they_invented`, `test_move_resolves_places`) that need the map, 1 root-only preflight, 3 to triage (EVAL-27).

**Tests that cannot fail / are vacuous here.** 46 Ville skips + 7 Postgres skips; `test_memory_verdict_is_honest` skips when RAM suffices; `test_the_memory_check_measures_this_machine.py:55` skips off-Windows; `test_every_recorder_is_wired[record_event]` skips the one recorder whose absence breaks §6; `eval_s4` 4.5/4.7 pass on absence; G5 passes by construction before a day; `test_genesis_subset` skips if `genesis_30.json` absent; `test_move_resolves_places` skips 4 when the genesis lacks named shops; `test_report_sections.py` proves the renderers with fixture dicts carrying the wrong event names (passes while production reads nothing).

**Mocks on both ends.** `test_run_genesis.py` (90 tests) drives the tick loop with `MockAgentGateway` + `MockSupervisor`/`mock_mode` (19 references): legitimate for wiring, but no test anywhere runs a real engine process through a world tick (Build Spec §1 Gate 2). `engine/tests/test_sim_pinned_boot.py` boots a real engine against a fake broker and is the closest thing to an end-to-end proof.

**Biggest holes relative to the live path.**
1. Composition roots: most API tests use `build_world_server` (`wiring.py:1410`, requires the map) while production boots `build_embedded_world_app` (`run_genesis.py:2102`); only `test_track_wiring.py`/`test_world_wiring.py` (17 references) touch both, and they fail here.
2. Tick loop: `dispatch_due_agents` (`run_genesis.py:4322`) with continuing actions, call budget, timeouts and the barren-tick guard — mock gateway only; the 500-storm that ended run 21 has no test.
3. Day boundary: `run_life_ticks`/`run_daily_upkeep`/head tax are unit-tested and "wired" by source inspection (`test_upkeep_is_wired.py`); no test drives the runner past tick 144 with a ledger and asserts transactions, events, dormancy and `assert_the_day_was_charged`.
4. Expansion pipeline: `test_world_map_expansion.py` + console integration, both skipped/failing without assets.
5. Construction: one end-to-end test (`test_construction.py:195`), skipped without assets; `build_structure` never called live.
6. Trace: no test asserts that a genesis run's `events` table receives world events or that `llm_calls` rows carry a tick.

---

## 10. Anything else (question 6)

- `genesis_20.json` `ablation_summary` (10 full) disagrees with its 20 agents — generated metadata that nothing validates.
- The `x-api-key` for evals is `government:treasury` — an eval spend shows up as treasury burn in the $1000 ledger; the report should subtract `purpose LIKE 'eval.%'` from "spend" or it inflates cost-per-agent-day.
- `series_report`/`loop_report` count G6 over `call_class='cognition'` but the classification is a prompt-marker heuristic (`purpose.py:49-57`); a changed summariser prompt (which `resident_voice.py` already rewrote) silently moves calls between classes. Pin the classifier with the real rewritten prompt.
- Run 23's operator note is the best evidence document in the repo and it is a hand-written comment above a generated body that contradicts it; the generated body should carry a "sections known broken" banner until W-1/W-2 land.
- The `[system]`/`[user]` summariser artefacts still appear in run 21's turn table (`run-21-report.md` n01 turn 5 "[system] Bram owes 400 sim") — P2's first-person leak detector would fire on that if it were ever run.

---

## 11. Open questions for the operator

1. Is `events` meant to be part of the record? If yes, W-1 with a domain filter; if no, the eval spec's E/S/T/C sections need a different source and the tool spec Part E is wrong.
2. Is DeepSeek in the study or not? DESIGN §4 says background on DeepSeek; the sim is Kimi-only; the learning centre's lane affects cost-per-agent-day and the C-series.
3. Energy per action: keep the sim-only pricing (as built) or implement DESIGN §3's USD cost per action? It changes what "mastery lowers the cost of living" can mean.
4. Should the L1 gate be run with the real composed prompt (W-5) before any more description work, given the harness's own numbers move by 2× with the prompt?
5. Will the next budget go to a night (W-14) rather than another morning? Every compounding instrument and half the proposed suite have no data until tick 144.
6. Do you want `table_amendment` (and an ideology→mechanics binding) before Round 3, given DESIGN §7's authoritarianism question is unreachable without it?
7. Which is canonical for population and wallets going forward: the ladder (10/50/250 at $2) or the series cast (20 at $6)? The docs disagree and the manifest (W-4) needs one answer.

---

## Appendix A — test runs performed (10-minute cap each)

1. `cd app && uv sync && uv run pytest -q -x --maxfail=20 -p no:cacheprovider` → `uv sync` OK; **collection error**: `broker/tests/test_broker_app.py` → `broker/app.py:335 create_app()` → `sqlite3.OperationalError: unable to open database file` (`/data/kasukabe.db`); 0 tests ran; exit 2.
2. Same with `TRACE_SQLITE_PATH=<scratch>/kasukabe.db`, `--maxfail=20` (no `-x`): 20 failed, 1,787 passed, 57 skipped, 31 s (stopped at maxfail). Side effect: a zero-byte `app/data/kasukabe.db` was created by a test using the default relative path; removed afterwards (gitignored).
3. Same, no maxfail, `--ignore=infra`, `-rs`: **110 failed, 3,145 passed, 68 skipped, 85 s**. First failures: `research/tests/test_demographics.py::test_it_measures_a_real_genesis_town`, nine `test_research_parquet_export.py` (DuckDB `sqlite_scanner` download 403), `tests/test_console_world_integration.py::…::test_approve_land_petition_actually_grows_the_map`, five `world/map/tests/test_the_town_is_walkable.py`, three `world/tests/test_agent_viewer_api.py`, `world/tests/test_business_pnl.py` (all Ville assets). Skip reasons: 46 Ville, 7 Postgres, 1 `record_event` excluded, 1 memory fits, 1 Windows hole, 1 movement None, 1 construction Ville, 7 genesis-shape.
4. `cd engine && uv run pytest -q -x --maxfail=20` → `ModuleNotFoundError: No module named 'pytest_asyncio'` at `tests/conftest.py:129`; exit 4. `uv sync --all-groups` did not install it (dev tools are an *extra*). Re-run after `uv sync --extra dev`: see Appendix A.5.
5. Engine, after `uv sync --extra dev`: `pytest --collect-only` → **4,441 tests collected in 2 s** (`addopts = "-p no:capture"`, `engine/pyproject.toml:64`). The full suite does **not** finish inside a 10-minute cap on this machine (my capped run produced no summary; a sibling reviewer's uncapped run of the same suite in this scratchpad was at 35 % after 15 minutes), so no full pass/fail count is reported. The sim-seam subset that ROADMAP Law 1 makes the changeable surface — `tests/test_sim_agent_adapter.py, test_sim_config.py, test_sim_fake_broker.py, test_sim_learning_gate.py, test_sim_memory_stream.py, test_sim_model_profile.py, test_sim_pinned_boot.py, test_sim_response_budget.py, test_sim_world_tools.py, test_resident_voice.py, test_session_log_captures_output.py, test_pinned_thread_enforcement.py` — ran to completion: **116 passed, 1 failed in 1.8 s**. The failure is `test_sim_agent_adapter.py::test_the_learn_route_consolidates_on_demand` (L136): it expects `learning_ran: True` from a fake engine whose batch wrote nothing, but `agent_adapter.py:168-176` now defines `learning_ran` as "something was encoded" (`knowledge_written: 0, errors: 0` come back with `learning_ran: False`). The honest-reporting fix landed without updating the test that pins the old meaning (EVAL-28).
