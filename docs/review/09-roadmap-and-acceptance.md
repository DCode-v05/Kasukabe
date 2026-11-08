# Roadmap: from a morning that talks to a town that runs itself

*The phased programme that orders every work item in docs 03 to 08. Each phase is a gate, not a date: no phase starts until the one before passes its acceptance twice on two fresh seeds at rung 1 (10 to 20 residents). Item ids refer to the tables in the spec documents (D = doc 03, G = doc 04 goals, E = doc 06, I = doc 07, C = doc 05 construction, R = doc 08).*

## Phase 0: one world, honest tests (about a week)

The precondition for everything. Without it every later fix lands on the
wrong host or on a test that cannot fail.

- One composition root. Either `GenesisRunner` absorbs `build_world_server`'s
  wiring (map service, crime engine, charter sink, trace event binding,
  social and viewer routers, WS) or `main.py` gains the engine routes and
  the tick loop; the other becomes a fixture (CORE item 13, ROADMAP T3).
  Recommended: the runner absorbs the wiring, one day's work, then decide
  whether `main.py` survives.
- One day-job module and one tick-job module called from the root: upkeep,
  tax, wages, pensions, payroll, inheritance, intention expiry, obligation
  accrual; election phases and tally, construction completion, zone
  regeneration, confinement release (E1, E2 skeleton, I1, C1).
- Behavioural tests replace every source-string test on the live path; a
  "has a production caller" test per engine; CI carries the Ville assets
  (R10).
- The record works: checkpoint mirror to the real database, `tick` on
  every call row, charter events on the bus, world events recorded, typed
  transaction reasons, run manifest (R1, R2, R4).
- Secrets out of git; the infra path boots (INFRA-04, INFRA-05).

Gate: a mock run crosses tick 144 on the single root, every day job fires
once at the boundary, `politics.election.scheduled` and
`economy.upkeep.charged` are in the trace on disk, and the suite is green on
a host without map assets by skipping, not failing.

## Phase 1: a complete, affordable day (about two weeks)

Doc 03 in full: memory stores wired, compaction stopped, tools under 4K
tokens, scaffolding stripped, Kimi markup parsed, real pacing, asynchronous
turns, skip the dormant, night bursted, drain wired, ledger-of-self
perception (D1 to D10). Then the first run with a night, then five days
(D11).

Gate: rung-1 run of two game-days at or below $0.15 per resident-day, p95
input tokens at or below 8K, every resident with an episode and a soul diff
after night one, no compaction marker on the wire, no paid turn discarded.

## Phase 2: a world that can be lived in (about three weeks)

- Canonical place ids and the business resolver; workshops belong to
  businesses; premises are real; genesis seeds tiles so every move walks;
  movement honesty; holdings in perception (E5, E7, TOOL-W1/W2/W9).
- Shelf and till; wage jobs produce; self-hire refused (E5).
- Obligation ledger; genesis debts; `pay_tax`; overdue to the desk (E4).
- Re-rate the currencies; size wallets, floats and treasury (E3).
- Food in kind, capacity at dispatch, treatment, inheritance, dormancy
  threshold, births, dials (E6).
- Composite tools validated, refunded, repeatable under `keep_doing`;
  confidential schemas confidential (TOOL-W7/W8).
- Goals, needs and beliefs, with sleep reflection (doc 04).

Gate: doc 06 §1 items 1, 3 and 6 (households' net worth rises; someone buys
energy from earnings; a seeded debt is visible and settles once); doc 04 §8
items 1 and 2; run 23's refusal classes `no business with id`,
`location:mill`, `you hold N not M` are empty in a replay.

## Phase 3: institutions that run (about three weeks)

Doc 07 in full: election tick job and civic notice, caretaker government,
case polarity and ground truth, amendment tools with a veto window,
permits, licences, jobs and tenders as tools with the treasury day job,
organisation membership, accounts, roles and decisions, trace writers for
petitions, organisations and cases (I1 to I6, I9, I11). Policy engine with
the four cheapest levers (E9).

Gate: doc 07 §1 items 1 to 5; a `price_caps` vote refuses the next
over-cap post with the rule quoted; the treasury's day shows salaries and
pensions paid.

## Phase 4: the town builds (about four weeks)

Doc 05 in full: one map on the production host with checkpointing, genesis
licences and real workshop sectors, plot and permit tools, the
construction site with continuable work and completion into a gated place,
tenders and contracts with escrow, expansion with purpose, need signals,
viewer map path and Public works panel, trace tables, materials sinks,
zoning (C1 to C13).

Gate: doc 05 §1, the whole chain, in a mock run and then in a live rung-1
run: a mill the town needed is tendered, built by hired hands under
`keep_doing`, paid by milestone, walked to, and used.

## Phase 5: market physics and ideology (about three weeks)

Market substrate: quantity on posts, stock in view, spoilage, control
businesses as price-takers from the rulebook, clearing venue,
`market_report` (E8). Full policy book: property rules, collective
ownership, licensing, borders, welfare, zoning as policy; structural
changes through amendment (E9, C13). Rulesets with the Charter as the
government's ruleset; temple as a RELIGION organisation; crime pipeline
wired or replaced (I7, I8). Ideology measurement: beliefs rows, platforms,
dream probes (E11).

Gate: doc 06 §1 items 4 and 5; an amendment abolishing private plots makes
`auction_plot` refuse and `grant_plot` the only path; a full election cycle
with a transfer of power whose winning platform is auto-tabled.

## Phase 6: the instrument (in parallel from phase 1, complete by phase 5)

Doc 08: raw payloads and reasoning, engine-mind checkpoints, nightly soul
and social exports, Parquet and Litestream, research routers on the running
host, township live bundle, one map, replay server and scrubber, the
evaluation suite with the `no_goals` arm and the control businesses
(R3, R5 to R9).

Gate: a run directory restores to any game-day with minds intact; every
metric in doc 08 §4 computes from the trace of a rung-1 run; every schema
table has a writer or an exclusion.

## Phase 7: rung 2 and rung 3

Fifty residents for five game-days with every mechanism on, then 250 for
twenty or more with the ablation arms, as ROADMAP §3 and §5 describe. The
compounding quantities (tokens per task falling, proficiency lowering cost,
tool diffusion, soul divergence) plus the growth quantities from doc 08
(structures completed by the town's own builders, need-to-completion time)
are the paper.

## The decisions that block the plan

Collected from the specs; each one changes what gets built. They belong to
the operator and should be answered before phase 1 ends.

1. One root: the runner absorbs the server's wiring, or `main.py` becomes
   the host (docs 02, 14).
2. History bounded by the seam, or the engine's compaction with a raised
   window (doc 03).
3. Kimi for everything, or DeepSeek for the learning centre and summariser;
   who pays for housekeeping (docs 03, 15).
4. `SIM_PER_USD` at 2,000 with a burn target, or 10,000 with prices scaled;
   fixed money supply or a production-tied mint (doc 06).
5. Genesis tax rate and salary schedule from `village_rules.json`, or none
   until the first government (docs 06, 07).
6. The President as a veto window with auto-approval, or a hard gate (docs
   05, 07).
7. Workshops at genesis as real buildings, or the town must build them
   (doc 05).
8. Bill of materials in processed goods, or raw (doc 05).
9. Justice: wire the built epistemic pipeline, or grow the simple model
   (doc 07).
10. Goals in the world (recommended) or in the engine's knowledge store
    (doc 04).
11. Kimi markup recovery at the broker or in the seam (doc 03).
12. The file-only township showcase kept, or the browser simulation deleted
    (doc 16).
13. Firecracker kept and proved on metal, or 250 processes on one host as
    the design (doc 15).
14. A Law 1 manifest for the engine edits, or revert (doc 10).

## How to read progress

Every phase gate is a test or a query over a completed trace, never a
mid-run number. A phase is done when its gate passes twice on two seeds at
rung 1. The run report should print the phase gates in its instrument table
so a run's place on this roadmap is visible in the run itself.
