# Spec: a world-class research instrument

*Cross-cutting specification drawn from `15-broker-trace-and-infra.md` (INFRA), `14-world-core-runner-and-record.md` (CORE), `10-engine-seam-and-mind.md` (ENG), `16-frontend-and-console.md` (UI), `13-governance-organisations-justice.md` (GOV) and `17-evals-tests-and-docs.md` (EVAL). The standard: every claim in a paper traceable to a trace row, every run reproducible from its artifact, every mechanism measured against a baseline and an ablation.*

## 1. The target, stated so it can be failed

- A run is one directory: manifest, trace database, checkpoints including
  the residents' minds, Parquet exports, raw payloads, and the code hashes.
  Anyone can restore Kasukabe to any game-day and carry it forward.
- Every table the schema declares has a live writer or an explicit
  exclusion, and a test that fails otherwise.
- Every call row carries the tick, the game day, the purpose, the lane and
  model that served it, the task label, and a pointer to its full payload.
- The viewer is a window, never an authority: nothing on screen is
  simulated in the browser over a real run.
- The evaluation suite measures thriving, economy, institutions, growth,
  goals and cost, each against the scripted controls and the ablation arms,
  from a completed trace, never from a live sample.

## 2. Where it stands today

| Fact | Finding |
|---|---|
| `world_snapshots` rows and checkpoint archives go to an in-memory database on every real run (two env names for one file) | INFRA-01, CORE-14 |
| `llm_calls.tick` and `task_label` are always NULL; per-game-day Parquet export of calls is empty by construction | INFRA-02 |
| No run manifest: code hashes, engine hash, model ARNs, prices, seeds and knobs are not recorded; engine minds are not checkpointed | INFRA-09, CORE-24 |
| Raw payloads are not archived; the 64 KB body cap fires on most calls and cuts the system prompt | INFRA-08 |
| World events are not recorded in a genesis run; six trace tables have no writer (`relationships`, `organizations`, `petitions`, `cases`, `buildings`, `eval_metrics`) | INFRA-16, INFRA-17, GROW-19 |
| Charter events go to a no-op sink on the production host | GOV-04, CORE-02 |
| The report reads the wrong event name for prices and cannot show upkeep because it is written as a transfer | TOOL-16, CORE-03, ECON-03 |
| Reasoning is never requested; the model reasons in-band and the record cannot separate reasoning from speech | INFRA-06, ENG-14 |
| The reasoning viewer, agent finder and scenario feed are unreachable on the host every run uses | UI-02, CORE-26 |
| The township page runs a 3,000-line in-browser simulation over the real run and can hide living residents | UI-03, UI-04 |
| The observatory is started by nothing | UI-11 |
| Parquet export and Litestream replication have no production caller | INFRA-10 |
| The refusal log covers under half of the refusal classes | TOOL-12 |
| Tests on the exact paths that are broken are source-string assertions that cannot fail | CORE-23, ECON-26, GOV-24, ENG-26 |
| A re-run against an existing trace database poisons the recorder | CORE-13 |
| The "frozen" engine directories carry post-vendoring edits without a manifest | ENG-25 |

## 3. The record

### 3.1 One run, one artifact

`runs/<run_id>/` with `manifest.json` (app and engine commits, genesis
sha256, seed, every `KASUKABE_*` and `SPEND_*` knob, price table, inference
profiles, lane capacities, ablation-arm assignment, start time), the trace
database, `checkpoints/` (world state plus a tarball of every resident's
`brain.sqlite`, `session_log.jsonl` and `memory_stream.jsonl` per game-day),
`exports/parquet/game_day=N/`, `payloads/` (gzipped request and response per
call with sha256), and the Litestream replica. `run_id` stamped on every
event, utterance, transaction and call row (INFRA-09, CORE-24).

### 3.2 Every call, every turn, every event

- `tick` and `game_day` on every broker row; `task_label` written back by
  the runner at turn close or dropped (INFRA-02).
- A `turns` table: run, tick, agent, sleeping, perception text, call
  budget, steps used, budget hit, succeeded, error, latency, rendered goal
  and belief ids (CORE-24, doc 04).
- World events recorded by an allowlist of domains (everything except
  per-tile movement and perception mirrors) with capped payloads; one event
  per resident per tick for walking rather than one per tile (INFRA-16,
  CORE-09).
- Writers for the six empty tables and the new ones (`goals`, `beliefs`,
  `sites`, `tenders`, `contracts`, `needs_snapshots`, `souls`); a nightly
  export of each engine's social graph and evolved soul into the trace
  (INFRA-17, INFRA-09).
- A schema version on events and a catalogue test that every emitted type
  is listed (CORE-24). A parametrised test that fails for any table with no
  live writer or explicit exclusion (INFRA-17).
- Typed transaction reasons so a bill, a wage, a fine and a gift are
  distinguishable (ECON-03).
- Reasoning requested and captured, or a documented "unsupported" result
  from one live probe (INFRA-06); Kimi markup recoveries counted (INFRA-07).
- The recorder's health on a route and in preflight; a run that cannot
  write its record halts, as ROADMAP says (CORE-24, INFRA-18).

### 3.3 Reproducibility

Seeds and the RNG state are checkpointed; LLM nondeterminism is accepted
and recorded (model, lane, inference profile, temperature defaults).
Determinism claims are limited to "same trace replays to the same world
state": a replay tool re-renders perceptions from the `turns` table and
re-applies tool invocations, and a test asserts the world state hash matches
the checkpoint at each day.

## 4. The evaluation suite

Layers as in `docs/kasukabeevaluationspec.md`: L1 verb reach on the real
model, L2 completed-run gates, L3 judges, L4 control arms. What to add,
each from a completed trace with source tables named:

| Area | Metric | Source |
|---|---|---|
| Thriving | household net worth day 1 vs day N (sim plus stock at posted prices); calories consumed per head-day; savings runway; proficiency per trade; tools invented and residents holding each | `transactions`, `events` (`economy.*`), `skills`, `tools_registry` |
| Economy | price convergence (coefficient of variation of asks per item across shops by day); elasticity (price response to stock-outs); wage to price ratio; Gini and mobility; money velocity; unemployment (residents with no wage and no continuing action); GDP per inference dollar | `events` (`economy.price.posted`, `economy.market.cleared`), `transactions`, `llm_calls` |
| Institutions | elections held and concluded; turnout; amendments tabled and passed; tax collected vs owed; budget executed (salaries, pensions, tenders paid); cases opened, verdicts, false-conviction rate against ground truth | `petitions`, `organizations`, `cases`, `events` (`politics.*`, `crime.*`), `transactions` |
| Growth | needs detected; petitions and approvals; tenders posted, bid, awarded; sites opened, labour hours, materials consumed; structures completed; tiles added; travel-time change from roads | `needs_snapshots`, `tenders`, `contracts`, `sites`, `buildings`, `world_snapshots` |
| Goals | goals set, done, expired, abandoned per resident-day; ticks to done by horizon; goal-to-action alignment; belief accuracy vs ground truth; life-goal drift day 1 vs N | `goals`, `beliefs`, `turns`, `tool_invocations` |
| Character | evolved-soul distance from genesis persona; ideology axes drift; judges P1 to P4 | `souls`, `memories`, `llm_calls` |
| Cost | tokens per task label per resident per day; calls avoided by `keep_doing` and by mastered workflows; spend per resident-day; p95 input tokens; turns discarded | `llm_calls`, `turns`, `tool_invocations` |

Baselines: the scripted control bakery (built, unwired: ECON-25) and the
other control businesses as price-takers (doc 06 §4.6); human baselines in
`docs/HUMAN_BASELINES.md`. Ablations: `no_learning` (honoured by the gate),
`no_reflection`, and a `no_goals` arm once doc 04 lands. Every gate is fixed
before the run and read from a completed store, never a mid-run sample
(Build Spec §10).

## 5. The viewer as a window

- Mount the research routers and `/ws/events` on the host that runs, so the
  agent finder, activity, reasoning viewer and scenario feed work against a
  real run (UI-02, UI-06, CORE-26).
- Split the township page into a live bundle that renders only world data
  and a demo bundle for the file-only showcase; gate the map on the world's
  alive flag and the world's clock, never the browser's (UI-03, UI-04,
  UI-17).
- One map for engine and viewer: the Tiled JSON as the single truth,
  served from `/map/tiled`, with construction and expansion drawn from
  events that carry tiles (UI-33, doc 05 §4.7).
- Research UI: a run registry, a replay server over the trace exposing the
  same `/world/*` routes at a chosen tick, a tick scrubber, per-agent
  goal and belief panel, economy time series (prices, wages, treasury,
  Gini), governance timeline, construction progress (UI §5).
- The observatory started by the run scripts against the run's database
  (UI-11); escape all world text (UI-09); one poller per tab (UI-08).

## 6. Test suite health

- Replace every source-string assertion on the live path with a behavioural
  test on a mock-mode runner (CORE-23, ECON-26, GOV-24).
- Wire-shape tests for the engine: tool count, prompt markers, input size;
  a boot-level test that a learning batch writes durable rows (ENG-26).
- Cross-package contract tests: broker ledger client against the embedded
  world app; console against both hosts (INFRA-25).
- CI carries the Ville assets so the map, walkability and construction
  tests run instead of failing or skipping (GROW-27, TOOL-27, CORE §test run).
- Portability: lazy broker app construction, no network at first test use,
  documented preconditions (INFRA-11, INFRA-30).
- A Law 1 diff manifest against the upstream engine commit (ENG-25).

## 7. Work items, in order

| # | Item | Findings | Effort |
|---|---|---|---|
| R1 | Checkpoint mirror to the real database; one env name; `tick` on every broker row; run manifest | INFRA-01/02/09, CORE-14/24 | S-M |
| R2 | World events recorded by allowlist; charter sink rewired; typed transaction reasons; report reads the right names | INFRA-16, GOV-04, CORE-02/03, ECON-03, TOOL-16 | S-M |
| R3 | Writers for the six empty tables and the new ones; table-coverage test; schema version and event catalogue test | INFRA-17, GROW-19, CORE-24 | M |
| R4 | `turns` table with rendered goal and belief ids; recorder health route; halt when the record breaks; re-run safety | CORE-13/24, INFRA-18 | S-M |
| R5 | Raw payload archive; reasoning probe and capture; markup recovery counted | INFRA-06/07/08 | M |
| R6 | Engine-mind checkpoints; nightly soul and social-graph export; Parquet scheduled; Litestream started | INFRA-09/10 | M |
| R7 | Research routers and WS on the production host; township live bundle; one map | UI-02/03/04/06/33 | L |
| R8 | Evaluation suite extensions in §4 with the control businesses and the `no_goals` arm | EVAL | M |
| R9 | Replay server, run registry, tick scrubber, economy and governance panels | UI §5 | L |
| R10 | Test-suite health: behavioural tests, wire-shape tests, contract tests, CI assets, portability, Law 1 manifest | CORE-23, ENG-25/26, INFRA-11/25/30, GROW-27 | M |
