# The review, checked against the code: every claim in docs 00 to 18

*Written 2026-09-05 against `Real` at `a6f95fa`, two days after the review was
written against `5400e94`. Every row below is a verdict on one claim the review
made, re-derived from the tree rather than read off `FINDINGS-REGISTER.md`.
Nothing in the code was changed; this document is the deliverable.*

## Legend

| | Meaning |
|---|---|
| ✅ | Done. The mechanism the review asked for exists in production code and I read it. |
| ⚠️ | Partial. Something landed; the finding's job is not finished. Counts as **not** complete in every total below. |
| ❌ | Not done. |

## Method, and its limits

Three passes, in this order.

1. **The changed-file lever.** `git diff --name-only 5400e94 HEAD -- app/ engine/`
   returns **40 non-test source files**. Any finding whose fix must live outside
   that set is provably untouched and is marked ❌ without further reading. That
   is where most of the 183 ❌ rows come from, and it is the strongest evidence
   here — a fact about the repository, not a judgement.
2. **Reading the fix.** For every finding in a changed file I read the code at
   the cited site and checked it against what the review asked for. Where the
   review asked for several things and some landed, the row is ⚠️ and the
   residual is named.
3. **Running the guards.** Full app suite: **3,207 passed · 7 skipped · 0 failed
   · 7 errors in 15 m 24 s** (`world/ broker/ supabase/`). All seven errors are
   `subprocess.TimeoutExpired: 'createdb' … timed out after 15 seconds` in
   `supabase/tests/test_supabase_migrations_apply_live.py` — a dormant Postgres
   path that errors where it should skip, which is `INFRA-22` itself. Targeted
   guards for the closed findings: 30 passed in 20 s.

**What this document does not do.** It does not run the town. A ✅ means the
code does what the review asked, proven against the tree and the tests. It does
not mean a live run behaves differently. Run 24 reached tick 80 of 144, so the
day boundary — the thing most of the economy hangs on — has still never fired
in a live run, and no ✅ below has been observed in one.

**Where this disagrees with the register.** `FINDINGS-REGISTER.md` stamps 52
findings FIXED. All 52 verified. There are **15 more** that are closed and carry
no stamp, because they were closed as side-effects of another fix and nobody
re-checked them: `ECON-02`, `ECON-19`, `ECON-26`, `GOV-07`, `GOV-14`, `TOOL-10`,
`CORE-03`, `CORE-10`, `CORE-14`, `GROW-12`, `GROW-16`, `EVAL-24`, `EVAL-28`,
`INFRA-16`, plus `GOV-24` as ⚠️. **No stamped row failed verification.**

---

## The whole picture

Docs 10 to 18 carry the 268 numbered findings. Docs 00 to 09 carry four other
kinds of claim, counted separately because they measure something different: the
findings say what is broken, the work-items say what was going to be built, and
the gates say whether it counts yet.

| Layer | Unit | ✅ | ⚠️ | ❌ | Total |
|---|---|---:|---:|---:|---:|
| Docs 10–18 | findings | **67** | **18** | **183** | **268** |
| Doc 00 | headline items | 12 | 3 | 5 | 20 |
| Doc 01 | blockers + leverage fixes | 2 | 4 | 9 | 15 |
| Docs 03–08 | work-items (D/E/G/I/R) | 8 | 17 | 28 | 53 |
| Doc 09 | phase gates | 0 | 0 | 8 | 8 |
| Doc 09 | operator decisions | 5 | 1 | 8 | 14 |

| Doc | Subsystem | ✅ | ⚠️ | ❌ | Total |
|---|---|---:|---:|---:|---:|
| `10` | Engine seam & mind (ENG) | 6 | 1 | 20 | 27 |
| `11` | World tools & physics (TOOL) | 14 | 3 | 13 | 30 |
| `12` | Economy & life (ECON) | 6 | 1 | 25 | 32 |
| `13` | Governance & justice (GOV) | 11 | 5 | 9 | 25 |
| `14` | Runner & record (CORE) | 10 | 3 | 20 | 33 |
| `15` | Broker, trace, infra (INFRA) | 4 | 2 | 24 | 30 |
| `16` | Frontend & console (UI) | 3 | 0 | 30 | 33 |
| `17` | Evals, tests & docs (EVAL) | 6 | 3 | 19 | 28 |
| `18` | Growth & construction (GROW) | 7 | 0 | 23 | 30 |
| | | **67** | **18** | **183** | **268** |

**The finding count flatters the state.** 25% of findings are closed; 15% of
work-items are; **zero of eight phase gates are** — because a gate needs every
item under it, and each phase has at least one item sitting on the map service,
the treasury, or the frontend.

---

# Doc 00 — The twenty findings that matter most

| # | Headline | ✔ | Where it stands |
|---|---|:--:|---|
| 1 | Two world servers; every run uses the one the roadmap said to retire | ❌ | The runner absorbed the crime engine and the charter sink. `MapService`, `social_api`, `agent_viewer_api` and the WS route are still only on `main.py` |
| 2 | The learning centre cannot write a single row on the sim path | ✅ | `pinned_boot.py:546-549` |
| 3 | Every model call is emergency-compacted | ✅ | `DISABLE_AUTO_COMPACT=1`, window 131072, history bounded in the seam |
| 4 | All ~79 tool schemas go on every call | ✅ | `FULL_SCHEMA_BUDGET=24`; herald's cache dropped after every refresh |
| 5 | Nobody can build anything | ✅ | plot, licence, completion caller and gate all landed |
| 6 | Land approval grows no map on the production host | ❌ | `map_service=None` at `run_genesis.py:2312` |
| 7 | The genesis election is never tallied | ✅ | `charter_tick` tallies at `run_genesis.py:5756` |
| 8 | A theft case is filed against the victim; the justice pipeline is never constructed | ⚠️ | Polarity fixed and the crime engine now exists on the running host; the justice tools still do not route through it |
| 9 | Head tax at zero; upkeep charged at the first tick | ✅ | 300 bps from the rulebook; the boot day is no longer billed |
| 10 | Every sim sink lands in a treasury that spends nothing | ⚠️ | Pensions pay. No payroll, tenders or relief — and the treasury holds 0 sim at genesis |
| 11 | At 10,000 sim per dollar energy is unbuyable from honest work | ✅ | `SIM_PER_USD = 5_000` |
| 12 | Place ids have five vocabularies and no resolver | ✅ | `resolve_business()` + `provides_workshops` |
| 13 | A business owner cannot stock the shelf or draw from the till | ✅ | four tools, priced and evented |
| 14 | Ticks unpaced, night dispatched, drain never fires, dormant refused | ✅ | all four |
| 15 | Paid turns are discarded at 120 s while the engine keeps billing | ❌ | the timeout is configurable and counted now; the turn is still abandoned mid-flight |
| 16 | In-memory checkpoints, no tick on calls, no world events, six empty tables, no manifest | ⚠️ | four of five done; the six tables are still empty |
| 17 | The run report reads event names the world never emits | ✅ | five names and two payload keys corrected, with a guard proven to fail |
| 18 | Kimi's text-form tool calls are parsed by nothing | ❌ | `tool_call_argument_begin` appears in no source file |
| 19 | The township page runs a 3,000-line browser simulation over the real run | ❌ | UI-03's hiding bug is fixed; the simulation stands |
| 20 | The tests on exactly the paths that are broken assert source text | ✅ | replaced with behavioural tests |

**12 ✅ · 3 ⚠️ · 5 ❌**

---

# Doc 01 — Run 23, evaluated against a real day

## The eight things that kept a morning from being a day

| # | Blocker | ✔ | Where it stands |
|---|---|:--:|---|
| 1 | Time never bit | ❌ | **Run 24 reached tick 80 of 144.** The boundary has still never been crossed live. Upkeep and tax are real (300 bps) and the night is bursted; hourly accrual and a shorter genesis runway were not built |
| 2 | Two geographies | ✅ | `resolve_business` folds every spelling; a shop's premises satisfies the workshops its trade needs |
| 3 | Walking is invisible and self-cancelling | ❌ | `rest` still cancels a journey unconditionally; the resident is still not told whether it walked or jumped |
| 4 | Thinking leaked into the world's silence | ❌ | Kimi markup unparsed; a prose-only turn still costs nothing and is still structurally reinforced |
| 5 | Speech outlives presence | ❌ | `core/perception.py` untouched |
| 6 | Debts lived only in prose | ❌ | genesis debts still not seeded into the ledger |
| 7 | Only thinking costs anything | ⚠️ | tax non-zero and the currency re-rated; `mine`'s real charge still unverified against a run's transactions |
| 8 | One tick holds a whole day of doing | ⚠️ | holdings are in perception now; 12 calls per tick unchanged |

*Related, and found after the review: the perception never named the place a
resident was standing in — not once in 23 runs — so every location gate was a
rule about a fact the resident could not see. Fixed at `af89ebf`. No reviewer
found it; it belongs next to blocker 3.*

## The seven fixes, in the leverage order the doc gave them

| # | Fix | ✔ |
|---|---|:--:|
| 1 | Hourly obligations; a sleep window inside the run; cut genesis runway; proportional drain reserve | ⚠️ night and the reserve landed; hourly accrual and runway did not |
| 2 | One canonical id per place, aliases resolved server-side | ✅ |
| 3 | Always walk | ❌ |
| 4 | Make silence cost and speech land | ❌ |
| 5 | Put debts and stock in the ledger | ⚠️ stock ✅, debts ❌ |
| 6 | One world-changing action per tick | ❌ |
| 7 | Reconcile the instrument | ✅ |

**The doc said items 1 to 4 alone would turn the morning into a day. One of the
four is done.**

---

# Doc 02 — State of the system

The 55-row designed/built/wired/proven matrix restates the findings, so it moves
with them. What changed:

| Claim | ✔ | Where it stands |
|---|:--:|---|
| §1 **"There are two world servers and the runs use the one the roadmap said to retire"** — the one structural fact | ❌ | Still true. Half the wiring was absorbed; the map service, the research routers and the WS route were not |
| Matrix rows that moved **no → yes** | — | **14**: soul and social stores, learning consolidation, canonical place ids, workshops as places, shelf and till, holdings in perception, elections, government tools, amendments, plots/permits/licences, construction completion, upkeep at the right boundary, head tax at a real rate, pacing/night/drain |
| Matrix rows still **built, not wired** | — | **17**, down from 24 |
| §3 cause 1 — two composition roots | ❌ | open, and it is the root of all three remaining P0s |
| §3 cause 2 — engines built as libraries with no tick driver | ✅ | elections, construction completion, confinement release, pensions and the learning drain all have production callers now. Zone regeneration and the crime pipeline still do not |
| §3 cause 3 — silent defaults and dead branches | ⚠️ | the zero tax rate, the in-memory database, the dead pacing knob and the storeless memory system are closed. `map_service=None`, `event_sink=None` and a fresh `PopulationDials` per tick remain |
| §3 cause 4 — tests that assert source text | ✅ | closed, and the pattern is now guarded by an AST check on the caller |
| §4 what is genuinely good | ✅ | unchanged and still true: the dispatcher, the ledger invariant, the broker, speech and addressees, the persona on the wire |

---

# Docs 03 to 08 — The 53 spec work-items

## Doc 03 — An affordable, complete day (D1–D11)

| Id | Item | ✔ | Residual |
|---|---|:--:|---|
| D1 | Durable memory stores in the seam; signal watermark | ✅ | — |
| D2 | Disable auto-compaction; seam conversation view; window knob | ⚠️ | compaction off and history bounded; the stored assistant turn is still prose, not an action record (ENG-09) |
| D3 | Tool schemas ≤ 4K on the wire; reset herald's cache | ✅ | — |
| D4 | Strip scaffolding; continuation cap; struggle reset | ❌ | `resident_voice` still rewrites only the summary prompt |
| D5 | Kimi markup parsed at the broker; prefixed names accepted | ⚠️ | prefixes accepted (TOOL-13); no parser anywhere |
| D6 | Real pacing; asynchronous turns; daily call budget; skip dead and dormant | ⚠️ | pacing ✅ and the dormancy skip ✅; turns are still synchronous and still discarded at the deadline |
| D7 | Night model: burst, suspend continuing actions, keep interrupts, learning off the critical path | ⚠️ | night bursted and quiet; the learning batch still runs inline before the turn |
| D8 | Day jobs at boundaries; drain from the continuous loop; proportional reserve | ✅ | — |
| D9 | Ledger-of-self perception; one open-questions mechanism; roster to STABLE | ⚠️ | holdings and the standing place landed; obligations, till, journey and the single mechanism did not |
| D10 | Behavioural tests replacing source-string tests; wire-shape assertions | ✅ | — |
| D11 | The first run with a night, then five days, at rung 1 | ❌ | run 24 stopped at tick 80 of 144 |

## Doc 04 — Evolving goals, needs and beliefs

Nothing was built. There is no `world/tools/primitives/goals.py`, no
`engine/src/sim/goal_reflection.py`, and no `Goal`/`Need`/`Belief` in
`tools/models.py` (`ENG-24` ❌).

| # | Acceptance clause | ✔ |
|---|---|:--:|
| 1 | A `food` need renders; `set_goal` renders a goal line; `buy` then `eat` closes it; one `goals` row per transition | ❌ |
| 2 | Two `craft` refusals produce a belief in the next prompt | ❌ |
| 3 | Sleep reflection posts `revise_goals` once per resident per night | ❌ |
| 4 | Every resident has a `week` goal with progress above zero by day 3 | ❌ |

## Doc 05 — Town growth and construction (G1–G13)

| Id | Item | ✔ | Residual |
|---|---|:--:|---|
| G1 | Completion and zone regeneration into both tick roots, with an AST wiring test | ⚠️ | completion wired (GROW-04); `regenerate_zones` still only on the unused host |
| G2 | One `WorldMap` and a `MapService` on the production host, checkpointed | ❌ | `self.map_service = None` — the single biggest open item in this document |
| G3 | Genesis licences for the yard; real workshop sectors; premises satisfy their workshops | ⚠️ | licence tool and `provides_workshops` ✅; the yard is still a bare record |
| G4 | Plot grant, auction, permit and licence tools; one `Plot` record | ⚠️ | `grant_plot` and `issue_builder_license` ✅; four notions of land remain |
| G5 | Construction site: `open_site`, `work_site`, `complete_site`; unified bill of materials | ⚠️ | completion and the gate landed; still a countdown, not labour; two bills of materials |
| G6 | Tenders, bids, contracts, inspections, escrow | ❌ | |
| G7 | Caretaker government or public petition; expansion resolution with veto window | ⚠️ | seats now fill so `petition` is reachable; no caretaker, no expansion resolution |
| G8 | Expansion with purpose: ownership, zones, corridors, delta payload | ❌ | |
| G9 | Viewer map path and Public works panel | ❌ | |
| G10 | Trace tables `petitions`, `buildings`, `sites`, `tenders`, `contracts` | ❌ | |
| G11 | Need signals and the `town_needs` tool | ❌ | |
| G12 | Materials sinks: durability, decay, repair, finite forest | ❌ | |
| G13 | Zoning as policy | ❌ | |

## Doc 06 — Economy, market and ideology (E1–E11)

| Id | Item | ✔ | Residual |
|---|---|:--:|---|
| E1 | Real head-tax rate; S1 checks sim not counts; day jobs at real boundaries; typed transaction reasons | ⚠️ | rate, gate and boundaries ✅; typed reasons ❌ (only `FINE` and `TILL` added) |
| E2 | `day_jobs.py` for both hosts with treasury outflows | ⚠️ | pensions pay; no module, no payroll, no relief, no tenders |
| E3 | Re-rate the currencies and size wallets, float and treasury | ⚠️ | rate re-rated to 5,000; wallets, float and treasury unsized — the treasury holds zero |
| E4 | Obligation ledger; genesis debts; `pay_tax`; overdue to the desk | ❌ | |
| E5 | Shelf and till; premises are real; workshops belong to businesses; holdings in perception | ✅ | the block that closed cleanest |
| E6 | Food in kind, capacity at dispatch, treatment, inheritance, dormancy threshold, births, dials | ❌ | all seven untouched |
| E7 | Honest prices in front of the resident | ❌ | |
| E8 | Market substrate: quantity on posts, stock in view, spoilage, clearing venue | ❌ | |
| E9 | `PolicyBook` with the four cheapest levers | ❌ | |
| E10 | Control bakery and `stakes_report` wired; behavioural tests | ⚠️ | `label_calls` has a caller and the tests landed; the control bakery is still unwired |
| E11 | Ideology measurement: beliefs rows, platforms, dream probes | ❌ | |

## Doc 07 — Institutions that run (I1–I11)

| Id | Item | ✔ | Residual |
|---|---|:--:|---|
| I1 | Election tick job, charter sink rewired, civic notice, canonical places into charter, candidacy fan-out | ✅ | all six findings closed |
| I2 | Caretaker government at genesis or public petition | ⚠️ | the problem it solved (vacant seats) is gone via I1; neither mechanism was built |
| I3 | Case polarity and ground truth; auto-release; fines as FINE | ⚠️ | all three landed; head-tax debt events still go to a `None` sink |
| I4 | `table_amendment`, `vote_amendment`, registry suffrage removed, veto window with auto-sign | ✅ | — |
| I5 | Permits, licences, jobs, tenders as tools; treasury day job; margin applied | ⚠️ | permits and licences ✅, pensions ✅; jobs, tenders and the margin ❌ |
| I6 | Organisation membership, account, roles, decisions | ❌ | |
| I7 | Rulesets with the Charter as the government's ruleset; temple as a RELIGION organisation | ❌ | |
| I8 | Wire or delete `world/crime/`; interview and warrant tools; checkpoint the store | ❌ | the engine is constructed now but nothing routes through it |
| I9 | Trace writers for `petitions`, `organizations`, `cases`; `social_api` mounted; one rulebook | ❌ | |
| I10 | Expansion resolution and veto window; approval opens a tender | ❌ | |
| I11 | Production-path wiring tests | ⚠️ | election, crime-engine and build tests exist; the charter and crime suites still test callerless engines |

## Doc 08 — Research instrumentation (R1–R10)

| Id | Item | ✔ | Residual |
|---|---|:--:|---|
| R1 | Checkpoint mirror to the real database; one env name; `tick` on every broker row; run manifest | ⚠️ | mirror, tick and manifest ✅; the manifest carries no engine hash, model ARNs or prices |
| R2 | World events by allowlist; charter sink; typed transaction reasons; report reads the right names | ⚠️ | three of four ✅; typed reasons ❌ |
| R3 | Writers for the six empty tables; table-coverage test; schema version | ❌ | |
| R4 | `turns` table; recorder health route; halt when the record breaks; re-run safety | ❌ | |
| R5 | Raw payload archive; reasoning probe; markup recovery counted | ❌ | |
| R6 | Engine-mind checkpoints; nightly soul export; Parquet scheduled; Litestream | ❌ | |
| R7 | Research routers and WS on the production host; township live bundle; one map | ❌ | 1 of 5 findings (UI-03) |
| R8 | Evaluation suite extensions with control businesses and the `no_goals` arm | ❌ | |
| R9 | Replay server, run registry, tick scrubber, economy and governance panels | ❌ | |
| R10 | Test-suite health: behavioural, wire-shape, contract tests, CI assets, portability, Law 1 manifest | ⚠️ | behavioural and wire-shape tests ✅; contract tests, CI assets, portability and the Law 1 manifest ❌ |

**53 items: 8 ✅ · 17 ⚠️ · 28 ❌.** The eight that closed cleanly — D1, D3, D8,
D10, E5, I1, I4 (and G-none) — are exactly the ones the two lanes' work blocks
were drawn around. Everything requiring the map service, the treasury, the
obligation ledger or the frontend is untouched.

---

# Doc 09 — Roadmap gates and the fourteen decisions

## The seven phase gates (plus rung 2/3)

| Phase | Gate | ✔ | Why |
|---|---|:--:|---|
| **0** | One root; one day-job module; behavioural tests; the record works; secrets out of git | ❌ | **two roots remain**; no shared `day_jobs.py`; typed transaction reasons absent; secrets still literals in `start-genesis.sh` |
| **1** | Two game-days ≤ $0.15/resident-day, p95 input ≤ 8K, an episode and soul diff per resident after night one, no compaction marker, no paid turn discarded | ❌ | no run has reached a night; paid turns still discarded at 120 s |
| **2** | Net worth rises; someone buys energy from earnings; a seeded debt settles; run-23's refusal classes empty | ❌ | there are no seeded debts to settle |
| **3** | A `price_caps` vote refuses the next over-cap post; the treasury's day shows salaries and pensions | ❌ | no policy engine; salaries unpaid |
| **4** | A mill is tendered, built by hired hands under `keep_doing`, paid by milestone, walked to and used | ❌ | no tenders, no site work, not walkable |
| **5** | An amendment abolishing private plots changes `auction_plot`; a winning platform auto-tabled | ❌ | |
| **6** | A run directory restores to any game-day with minds intact; every table has a writer or an exclusion | ❌ | |
| **7** | Rung 2 (50 for five days) then rung 3 (250 for twenty) | ❌ | |

**Phase 0 — the precondition for everything — has not passed.** Its two hardest
clauses, one root and one day-job module, are exactly what `GROW-06`, `UI-01`,
`UI-02` and `ECON-08` still block.

## The fourteen decisions that block the plan

| # | Decision | ✔ | State |
|---|---|:--:|---|
| 1 | One root: the runner absorbs the server's wiring, or `main.py` becomes the host | ⚠️ | **decided** (runner absorbs); half-built |
| 2 | History bounded by the seam, or the engine's compaction with a raised window | ✅ | decided **and** built |
| 3 | Kimi for everything, or DeepSeek for the learning centre; who pays for housekeeping | ○ | open |
| 4 | `SIM_PER_USD` at 2,000 with a burn target, or 10,000 with prices scaled | ✅ | decided **and** built — landed at **5,000**, not the 2,000 the spec proposed. `money.py:57` dates it 2026-09-04. If 2,000 was the number given, the code does not match it |
| 5 | Genesis tax rate and salary schedule from `village_rules.json`, or none until the first government | ✅ | decided **and** built (300 bps). Salaries still browser-only |
| 6 | The President as a veto window with auto-approval, or a hard gate | ✅ | decided **and** built (auto-sign after `--veto-window-ticks`, default 0) |
| 7 | Workshops at genesis as real buildings, or the town must build them | ✅ | decided **and** built (business premises provide workshops) |
| 8 | Bill of materials in processed goods, or raw | ○ | open |
| 9 | Justice: wire the built epistemic pipeline, or grow the simple model | ○ | open |
| 10 | Goals in the world, or in the engine's knowledge store | ○ | open |
| 11 | Kimi markup recovery at the broker or in the seam | ○ | open |
| 12 | The file-only township showcase kept, or the browser simulation deleted | ○ | open |
| 13 | Firecracker kept and proved on metal, or 250 processes on one host | ○ | open |
| 14 | A Law 1 manifest for the engine edits, or revert | ○ | open |

**5 decided and built · 1 decided and half-built · 8 still with the operator.**

---

# Doc 10 — Engine seam and the resident's mind (ENG)

**6 ✅ · 1 ⚠️ · 20 ❌ of 27**

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| ENG-01 | P0 | ✅ | `pinned_boot.py:546-549` builds `EpisodicMemory/KnowledgeStore/SocialGraph/Soul`; passed to `MemorySystem` at `:631` |
| ENG-02 | P0 | ✅ | `sim/config.py:288` `DISABLE_AUTO_COMPACT=1`; `bounded_history` wired at `pinned_boot.py:82`; `SIM_CONTEXT_WINDOW` raised to 131072 |
| ENG-03 | P1 | ✅ | `registry.py:259` `FULL_SCHEMA_BUDGET=24`; `sim/tool_surface.py` prunes engine-only tools from a resident's surface |
| ENG-04 | P1 | ✅ | `bounded_signals.WatermarkedSignalStore`, wired at `pinned_boot.py:83` |
| ENG-05 | P1 | ❌ | `pipeline.py:877` still `type='correction' AND source='user'`; `world_corrections.py:76` still writes `"engine"` |
| ENG-06 | P1 | ❌ | no `skill_catalog` anywhere in `pinned_boot.py` |
| ENG-07 | P1 | ❌ | `resident_voice.py` rewrites only the frozen summary prompt; (a) to (j) are all still on the wire |
| ENG-08 | P1 | ❌ | `tool_call_argument_begin` appears in no source file in the repository |
| ENG-09 | P1 | ❌ | history is bounded, but the stored assistant turn is still `(perception, full_text)` |
| ENG-10 | P3 | ❌ | no per-agent orphan counter in `translate.py` |
| ENG-11 | P1 | ❌ | `run_genesis.py:867` still defines `tick(agent_id, perception_text)`; the runner calls `send_tick` |
| ENG-12 | P1 | ❌ | `tick_timeout_s` is configurable and timeouts are counted; the turn is still abandoned mid-flight and keeps billing |
| ENG-13 | P2 | ⚠️ | the sim suite is green; `/tick` and `/learn` still mean two different things by `learning_ran` (now documented at `agent_adapter.py:184`) |
| ENG-14 | P2 | ❌ | `memory_stream.py:165` still accumulates `ThinkingDelta` only |
| ENG-15 | P2 | ❌ | `memory_query.py` still passes the raw string to FTS5 `MATCH` |
| ENG-16 | P1 | ✅ | `pinned_boot.py:369-373` resets `cached_tool_names` after a forced refresh |
| ENG-17 | P2 | ❌ | `agent_adapter.py` still awaits `learning_gate.on_tick` before `send_tick` |
| ENG-18 | P3 | ❌ | `world_tools.py:193` still records every `status_code >= 400`, 5xx included |
| ENG-19 | P3 | ❌ | no ETag on the per-tick registry fetch |
| ENG-20 | P3 | ❌ | `_tighten_compaction` still defined (`:339`) and still called (`:410`) |
| ENG-21 | P2 | ❌ | no continuation cap in the seam |
| ENG-22 | P2 | ❌ | no zeroed `_struggle` written at `send_tick` |
| ENG-23 | P3 | ❌ | `thread.py:4278` still `subject=user_input[:40]` |
| ENG-24 | P1 | ❌ | no goal / need / belief representation |
| ENG-25 | P2 | ❌ | no upstream diff manifest in `engine/implementation-notes.md` |
| ENG-26 | P2 | ✅ | `test_sim_learning_can_write.py`, `test_sim_what_reaches_the_wire.py`, `test_sim_a_redescribed_tool_reaches_the_wire.py` — all three the review asked for |
| ENG-27 | P2 | ❌ | the broker still writes `cache_read_input_tokens: 0`; no cache points in Converse translation |

---

# Doc 11 — World tools and physics (TOOL)

**14 ✅ · 3 ⚠️ · 13 ❌ of 30** — the strongest lane.

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| TOOL-01 | P0 | ✅ | `place_aliases.resolve_business()` `:314` folds key, prefix, name, premises and a distinctive word; declines rather than guesses |
| TOOL-02 | P0 | ✅ | `Business.provides_workshops` (`models.py:55`) + `satisfies_gate_in()` (`:182`) |
| TOOL-03 | P0 | ✅ | `stock_shelf` / `take_from_shelf` / `deposit_to_till` / `draw_from_till`, `commerce.py:834-977` |
| TOOL-04 | P0 | ✅ | `inventory.holdings_line()` in the standing brief (`run_genesis.py:3957`) and `look_around` (`places.py:344`) |
| TOOL-05 | P1 | ❌ | `rest_handler` still cancels a journey unconditionally; `agent.moved` carries no `target_agent_id`; no walk/jump distinction in the result |
| TOOL-06 | P1 | ❌ | `validate_steps` (`meta.py:104-137`) unchanged: no `params_model` dry-run, no `require_recipe`, no composite refund, no `move` ban |
| TOOL-07 | P1 | ❌ | `tool_registry_rows` still has no visibility filter; every confidential tool's name and schema still goes to every engine |
| TOOL-08 | P1 | ✅ | `place_for_charter()` at nine handler call sites; `CharterError` classified as a refusal |
| TOOL-09 | P1 | ✅ | `attach_utterance_log` moved to `bootstrap.py:320`, inside `build_world` |
| TOOL-10 | P0 | ✅ | **not stamped** — `grant_plot` + `issue_builder_license` tools, completion driven from `run_genesis.py:5823`, and a completed building satisfies its purpose gate |
| TOOL-11 | P1 | ✅ | `register_business` takes a validated `premises` (`civic.py:141-180`) |
| TOOL-12 | P2 | ⚠️ | `CharterError` subclasses classified; `NotFoundError`, `RecipeNotFoundError` and `InsufficientInventoryError` still fall through |
| TOOL-13 | P2 | ✅ | both fold via `canonical_tool_ref` (`living.py:393`, `intent.py:68`) |
| TOOL-14 | P2 | ✅ | `_recipe_menu()` in both descriptions (`production.py:97,135,150`); `harvest` untouched, as the register said |
| TOOL-15 | P2 | ✅ | self-hire refused (`civic.py:374`) **and** consent required (`:365`) — more than the register claimed |
| TOOL-16 | P2 | ✅ | `run_report.py:1253` reads `economy.price.posted` |
| TOOL-17 | P2 | ❌ | `core/perception.py` still keys on `listener_agent_id` alone; questions still outlive the asker |
| TOOL-18 | P2 | ❌ | no genesis `loans` seeding; `check_debts` still cannot know a genesis debt |
| TOOL-19 | P2 | ✅ | `satisfies_gate` in `commerce.py:513` and `crime.py` |
| TOOL-20 | P2 | ❌ | `regenerate_zones` still only at `wiring.py:89`, never in `run_genesis` |
| TOOL-21 | P2 | ⚠️ | the world reads `village_rules.json` for the genesis tax rate; wages, terms and salaries are still browser-only |
| TOOL-22 | P2 | ✅ | `release_served_sentences` swept every tick (`run_genesis.py:5116`), with a test that fails when the call site goes |
| TOOL-23 | P3 | ❌ | no `check_location_gate` dry-run at `keep_doing` commit |
| TOOL-24 | P3 | ❌ | `registry.py:320` still prints the gate in the tools' vocabulary |
| TOOL-25 | P3 | ⚠️ | `is_real_destination` has a caller (`civic.py:177`); `task_label.py` and `render_economic_context_block` are still dead |
| TOOL-26 | P3 | ❌ | `places.py:372` still carries strategy in the description |
| TOOL-27 | P3 | ❌ | no session fixture skipping map-dependent tests |
| TOOL-28 | — | ❌ | still unverified against a run's `transactions` rows |
| TOOL-29 | P3 | ❌ | `rule_for` still falls through to `default_rule` for a composite's own name |
| TOOL-30 | P1 | ❌ | no `join_org` / `admit_member` / `leave_org` / `expel` tool |

---

# Doc 12 — Economy and life (ECON)

**6 ✅ · 1 ⚠️ · 25 ❌ of 32** — the weakest lane relative to its P0 count.

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| ECON-01 | P0 | ✅ | `charter/genesis.py:46` reads 300 bps from `village_rules.json`; the gate asserts sim moved, not households visited |
| ECON-02 | P1 | ✅ | **not stamped** — closed by CORE-04: `run_genesis.py:3469` seeds `last_life_day_processed` to the boot day, on the fresh path only |
| ECON-03 | P2 | ❌ | `TransactionReason` gained `FINE` and `TILL`; no `UPKEEP`, `ACTION_COST`, `REFUND`, `FEE`, `DONATION` |
| ECON-04 | P0 | ❌ | pensions pay; no payroll, tenders or relief — and the treasury holds **zero sim** at genesis while residents hold ~1.57 M |
| ECON-05 | P0 | ✅ | `money.py:57` `SIM_PER_USD = 5_000` |
| ECON-06 | P1 | ❌ | `effective_work_capacity` still read by nothing outside `upkeep.py` |
| ECON-07 | P1 | ❌ | `Ledger.settle_inheritance` still has no caller |
| ECON-08 | P1 | ❌ | `wiring.py` still has no upkeep, tax, wages or zero-wallet sweep |
| ECON-09 | P1 | ✅ | `pay_pension` called at `run_genesis.py:5252` |
| ECON-10 | P1 | ❌ | `Job.salary_sim` still moves no money |
| ECON-11 | P2 | ❌ | `run_genesis.py:5098` still builds a fresh `PopulationDials` every tick |
| ECON-12 | P1 | ❌ | `treat_at_pharmacy` still has no tool |
| ECON-13 | P1 | ❌ | genesis debts still never reach the ledger |
| ECON-14 | P1 | ❌ | `repay_head_tax_debt` still has no caller; no `pay_tax` tool |
| ECON-15 | P2 | ❌ | `arrears["upkeep"]` still never written |
| ECON-16 | P1 | ❌ | `run_genesis.py:3620` still resets the hunger ladder on every `agent.ate` |
| ECON-17 | P1 | ❌ | `quote_action_cost_handler` still does not normalise `world:` / `world_` |
| ECON-18 | P2 | ❌ | `distance_tiles` never resolved in `registry.py`; `move` still charged its floor |
| ECON-19 | P2 | ✅ | **not stamped** — `who_pays_for()` (`run_genesis.py:4757`): the richest holder pays, deterministically |
| ECON-20 | P1 | ❌ | `wallet_line` still never sets `upkeep_due_sim` / `head_tax_due_sim` |
| ECON-21 | P1 | ❌ | `run_genesis.py:5543` still `usd_balance(agent_id) > 0` — the one-cent dead zone stands |
| ECON-22 | P2 | ❌ | `life/tick.py` and the runner's inline copy both live |
| ECON-23 | P2 | ❌ | no `request_child` tool; births unreachable |
| ECON-24 | P2 | ❌ | `soul_seed.py` still states rules without upkeep, tax or revival numbers |
| ECON-25 | P2 | ⚠️ | `stakes_report.label_calls` called from `run_report.py:796`; `control_bakery` still unwired |
| ECON-26 | P3 | ✅ | **not stamped** — `test_upkeep_is_wired.py` rewritten behavioural under CORE-23 |
| ECON-27 | P3 | ❌ | `civic.py:552` still says "Cost: 2 sim-currency" against a schedule of 0 |
| ECON-28 | P3 | ❌ | `gather_wallet_and_burn` still scans the whole transaction log |
| ECON-29 | P3 | ❌ | `economy_bridge.py` still routes every life fee to the treasury |
| ECON-30 | P3 | ❌ | `credit_store_margin_bps` still read only inside charter |
| ECON-31 | P3 | ❌ | household-less agents still skipped by upkeep |
| ECON-32 | P2 | ❌ | the run-cap vs wallet arithmetic is unchanged; run 24 ran at $5 |

---

# Doc 13 — Governance, organisations and justice (GOV)

**11 ✅ · 5 ⚠️ · 9 ❌ of 25**

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| GOV-01 | P0 | ✅ | `charter_tick` (`run_genesis.py:5704`) advances the phase and tallies at `:5756`; proven to fail when the call site is deleted |
| GOV-02 | P0 | ✅ | `victim_agent_id` + write-once `ground_truth_perpetrator_id` (`models.py:419-420`); `outcome` and `verdict` split |
| GOV-03 | P0 | ✅ | `table_amendment_handler` `:648`, `vote_amendment_handler` `:693`; President as a veto window |
| GOV-04 | P1 | ✅ | `rewire_charter_event_sink` called at `run_genesis.py:4100` |
| GOV-05 | P1 | ⚠️ | the crime engine is now built on the running host (`build_the_crime_engine`, `:4031`); the justice tools still do not route through it, and "police" still has three definitions |
| GOV-06 | P1 | ✅ | same change as ECON-01 |
| GOV-07 | P1 | ✅ | **not stamped** — `grant_plot` mints `Plot(permitted=True)` (`institutions.py:179-184`) and `issue_builder_license` is a tool (`:213`) |
| GOV-08 | P1 | ⚠️ | pensions paid; salaries, police wages and tenders still move no money |
| GOV-09 | P1 | ❌ | credit-store margin and pension policy are still dead levers |
| GOV-10 | P1 | ❌ | no membership, treasury, roles, decisions or rulesets for organisations |
| GOV-11 | P1 | ❌ | `approve_petition` still operator-only (`admin_api.py:632`) |
| GOV-12 | P1 | ❌ | no checkpoint registration for head-tax debt or the crime store |
| GOV-13 | P2 | ✅ | `vote` drops its registry `min_age`; the charter decides suffrage |
| GOV-14 | P2 | ✅ | **not stamped** — same `who_pays_for()` as ECON-19 |
| GOV-15 | P2 | ⚠️ | fines are `TransactionReason.FINE`; head-tax debt still emits to `event_sink=None` (`:5311`) |
| GOV-16 | P2 | ✅ | `release_served_sentences` reads `until_tick` (`:5865`) |
| GOV-17 | P2 | ✅ | civic notice in the perception (`retrieval_depth.py:975-998`): election, phase, candidates, your ballot, who governs, the tax rate |
| GOV-18 | P2 | ⚠️ | the tax rate comes from the rulebook; salaries, term length and the rest are still browser-only |
| GOV-19 | P2 | ❌ | `build_social_router` still only at `main.py:199` |
| GOV-20 | P2 | ✅ | `involved_agent_ids` is the candidate alone (`institutions.py:328`) |
| GOV-21 | P2 | ❌ | `LOCATION_JAIL = LOCATION_TOWN_HALL` unchanged; the map cannot draw it |
| GOV-22 | P2 | ❌ | `ComplaintLedger.file_complaint` still uncalled |
| GOV-23 | P3 | ❌ | `issue_license` still records a `fee_paid_sim` it never debits |
| GOV-24 | P3 | ⚠️ | production-path tests now exist (charter bus, crime engine, tally, amendment, build); the broader charter and crime suites still test engines with no runtime caller |
| GOV-25 | P3 | ❌ | `is_resident = household_id is not None` unchanged (`civic.py:83`) |

---

# Doc 14 — World core, runner and record (CORE)

**10 ✅ · 3 ⚠️ · 20 ❌ of 33**

| Id | ✔ | Evidence |
|---|:--:|---|
| CORE-01 | ✅ | `pace_the_tick` sleeps the remainder of the interval (`run_genesis.py:6155-6193`) and counts overruns |
| CORE-02 | ✅ | `rewire_charter_event_sink` at `:4100` |
| CORE-03 | ✅ | **not stamped** — `record_event` re-attached behind `RECORDED_EVENT_DOMAINS` (`:542`, `:3565`); report event names corrected under EVAL-01 |
| CORE-04 | ✅ | boot day seeded (`:3469`); the phantom day-0 charge is gone |
| CORE-05 | ✅ | `drain_before_halt` in `run_continuous` (`:6212`); the reserve is now the most expensive tick this run has had (`:6233`) |
| CORE-06 | ❌ | a fresh `PopulationDials` per tick; the console's dials never reach the life tick |
| CORE-07 | ❌ | `apply_tick_pacing` still only inside `seed_genesis` (`:3789`); a resume skips it |
| CORE-08 | ❌ | `hunger_by_agent`, `head_tax_debts` and `day_jobs_ran` still unregistered with the checkpointer |
| CORE-09 | ❌ | `EventBus` still `buffer_size=2000`; `agent.moved` still one event per tile |
| CORE-10 | ✅ | **not stamped** — dispatch skips dead **and** dormant (`:4847`; `is_dormant` at `:4819` covers both). Residual: `unregister_agent` still uncalled and the engine process is not stopped |
| CORE-11 | ❌ | no in-flight set, no 409; a timed-out turn still finishes against a stale world |
| CORE-12 | ❌ | `due_per_tick` still counts one call per due resident |
| CORE-13 | ❌ | `known_agent_ids` unchanged; a re-run against an existing store still poisons the recorder |
| CORE-14 | ✅ | **not stamped** — the mirror uses `self.trace_store.client` (`:4169`) |
| CORE-15 | ❌ | two disagreeing unanswered-question mechanisms remain |
| CORE-16 | ⚠️ | holdings and the standing place are told now; obligations, storage, till, journey and commitments are not |
| CORE-17 | ✅ | `sleeps_through` (`:4773`), `night_is_quiet` (`:6144`), night bursted, interrupts kept |
| CORE-18 | ❌ | `last_health_check_at=None` is never stamped (`:1209`); a wedged engine is still never restarted |
| CORE-19 | ❌ | two `GET /world/state` definitions (agents_api `:509`, location_api `:737`) |
| CORE-20 | ❌ | same as ENG-11 — the namespace backend would crash on first dispatch |
| CORE-21 | ❌ | no `agent.spawned` at genesis; two location indexes written by hand |
| CORE-22 | ❌ | per-agent registry re-served and schemas regenerated every tick |
| CORE-23 | ✅ | source-string tests replaced with behavioural ones on a mock runner |
| CORE-24 | ✅ | `write_the_run_manifest()` at `:3474` — git sha, genesis sha256, seed, model id, knobs |
| CORE-25 | ⚠️ | the drain's reserve is dynamic now; the governor and the broker cap are still the same number |
| CORE-26 | ❌ | `agent_viewer_api` not mounted on the host that runs |
| CORE-27 | ❌ | `checkpoint_every_day` still inert; `world_mode="external"` still accepted |
| CORE-28 | ❌ | `addressed_agent_ids` still unread by the activity feed and the report's thread detection |
| CORE-29 | ❌ | same as ENG-05 |
| CORE-30 | ❌ | no `X-Kasukabe-Purpose` header from the engine; classification still inferred |
| CORE-31 | ⚠️ | the resident is now told the place it stands in (`format_ambient`); still no tile at genesis, so the first move is still a jump |
| CORE-32 | ❌ | still measured in ticks (now env-configurable via `KASUKABE_BARREN_TICKS`) |
| CORE-33 | ❌ | `wait_for_engines_ready` still sequential over the port range |

---

# Doc 15 — Broker, trace and infra (INFRA)

**4 ✅ · 2 ⚠️ · 24 ❌ of 30**

| Id | ✔ | Evidence |
|---|:--:|---|
| INFRA-01 | ✅ | the checkpoint mirror uses the runner's resolved trace client, not a second in-memory one |
| INFRA-02 | ✅ | `broker/ledger.py:72` carries `tick`; `run_genesis.py:2620` returns `world_state.tick()` |
| INFRA-03 | ✅ | dormancy skip inside `dispatch_due_agents` (`:4847`), before the perception is built |
| INFRA-04 | ❌ | `app/infra/` untouched; the systemd path still cannot boot the current broker |
| INFRA-05 | ❌ | shared secrets still literals in `start-genesis.sh`; the AWS keys pasted in chat still need rotating |
| INFRA-06 | ❌ | reasoning still carried but never requested |
| INFRA-07 | ❌ | no Kimi text-form parser at the broker |
| INFRA-08 | ❌ | no payload archiving; the 64 KB body cap unchanged |
| INFRA-09 | ⚠️ | the manifest is written; engine hash, model ARNs, prices and engine-mind checkpoints are still absent |
| INFRA-10 | ❌ | `export_game_day` still has no production caller |
| INFRA-11 | ❌ | `broker/app.py:335` still `app = create_app()` at import time |
| INFRA-12 | ❌ | throughput pacing math unchanged |
| INFRA-13 | ❌ | the pre-dispatch estimate still excludes tool schemas and tool JSON |
| INFRA-14 | ❌ | `spent_nano_dollars` still starts at 0 on every boot |
| INFRA-15 | ❌ | `asyncio.to_thread` per stream event unchanged |
| INFRA-16 | ✅ | **not stamped** — world events recorded under a domain allowlist |
| INFRA-17 | ❌ | `relationships`, `organizations`, `petitions`, `cases`, `buildings`, `eval_metrics` still have no writer |
| INFRA-18 | ❌ | no `/admin/trace` poll in `broker_terminal_reason` |
| INFRA-19 | ❌ | learning still billed to the resident and run mid-morning |
| INFRA-20 | ❌ | the Firecracker track is still unwired and unproved |
| INFRA-21 | ❌ | IAM model ARNs still unverified |
| INFRA-22 | ❌ | dormant Postgres migrations remain — **and this is the only thing the suite is not green on**: seven `createdb` timeouts where the test should skip |
| INFRA-23 | ❌ | the mock still streams a `contentBlockStart` real Bedrock omits |
| INFRA-24 | ❌ | `RateLimiterRegistry` and `SupabaseRestSink` still orphaned with tests that cannot fail |
| INFRA-25 | ❌ | no cross-package test drives `HttpLedgerClient` against the embedded world app |
| INFRA-26 | ⚠️ | broker rows carry `tick` now; world `events` rows still do not |
| INFRA-27 | ❌ | `content_loss` still has no consumer |
| INFRA-28 | ❌ | `docker-compose.yml` still references services that do not exist |
| INFRA-29 | ❌ | same as ENG-12 / CORE-11 |
| INFRA-30 | ❌ | test-suite portability unchanged |

---

# Doc 16 — Frontend and console (UI)

**3 ✅ · 0 ⚠️ · 30 ❌ of 33** — the least-touched subsystem in the repository.

`game/server.py`, `game/world_client.py`, `game/console/`, `game/realsim.py`,
`agents_api.py`, `location_api.py`, `agent_viewer_api.py` and `social_api.py`
are **all unchanged since the review baseline**. The only frontend files that
changed are `township.js`, `township-v2.js`, `village.js` and `village2.js` —
and most of that change is the temple, a defect no reviewer found.

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| UI-01 | P1 | ❌ | `map_service=None` (`run_genesis.py:2312`); neither viewer can show growth |
| UI-02 | P1 | ❌ | the DESIGN §8 research viewers are still mounted only on `main.py` |
| UI-03 | P1 | ✅ | `isAlive()` defers to `LIVE.alive` when bound (`township-v2.js:687-692`) |
| UI-04 | P1 | ❌ | the ~3,000-line in-browser simulation still paints over a real run |
| UI-05 | P1 | ❌ | nothing writes `AdminExtras.birth_requests` on the embedded host |
| UI-06 | P1 | ❌ | the embedded app still has no `/ws/events`; the Phaser page is dead against it |
| UI-07 | P2 | ❌ | dead badge branch and duplicated HUD writes remain |
| UI-08 | P2 | ❌ | `/api/township/live` still polled from two places |
| UI-09 | P2 | ✅ | `esc()` widened to `'` and five raw insertion points wrapped (`:4172-4177`) |
| UI-10 | P2 | ❌ | `_cast_cache` still keyed on the id set alone |
| UI-11 | P2 | ❌ | the observatory is still part of no run |
| UI-12 | P2 | ❌ | `/agents/{id}` detail still thrown away by the game client |
| UI-13 | P2 | ❌ | `MockWorldClient` still does not satisfy `WorldClient` |
| UI-14 | P2 | ❌ | `realsim.py:237` still imports `world/`, against HANDOVER §3 rule 2 |
| UI-15 | P2 | ❌ | the Real-sim button still needs a `broker-real` service that does not exist |
| UI-16 | P2 | ❌ | `console.js:463` still hard-codes `RUN_BUDGET_CAP_USD_CENTS = 40000` |
| UI-17 | P2 | ✅ | `state.hourFrac = LIVE.tick / 6`; the day counts from zero like the world's (`:5304-5316`) |
| UI-18 | P2 | ❌ | `/places` on the production host still has no geometry |
| UI-19 | P2 | ❌ | the route-order bug over the 1.7 MB `agents.js` remains |
| UI-20 | P2 | ❌ | a Real-sim run and the base pages still read different worlds |
| UI-21 | P2 | ❌ | stale docstrings unchanged |
| UI-22 | P2 | ❌ | cross-origin defaults and CORS unchanged |
| UI-23 | P2 | ❌ | tautological tests remain |
| UI-24 | P3 | ❌ | `talk.js` still hard-codes IST |
| UI-25 | P3 | ❌ | two owners of the resident-file panel |
| UI-26 | P3 | ❌ | `shell.js` still monkey-patches five globals |
| UI-27 | P3 | ❌ | `build_agents.py` **and** `build_characters.py` both still write `agents.js` |
| UI-28 | P3 | ❌ | the 4,469-line pure-fiction v1 is still reachable at `/township.html` |
| UI-29 | P3 | ❌ | 400 ms full re-renders remain |
| UI-30 | P3 | ❌ | `contracts.py` still documents a contract nobody implements |
| UI-31 | P3 | ❌ | `renderPetition` still offers only edge and depth |
| UI-32 | P3 | ❌ | alerts still recomputed from the 2,000-entry ring |
| UI-33 | — | ❌ | one map for engine and viewer: still a design question, decision 12 |

---

# Doc 17 — Evals, tests and docs (EVAL)

**6 ✅ · 3 ⚠️ · 19 ❌ of 28**

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| EVAL-01 | P0 | ✅ | five of six event names and two payload keys corrected; the guard was proven to fail on the old name. Every economic claim in every run report came through this |
| EVAL-02 | P0 | ✅ | `events` written under the domain allowlist |
| EVAL-03 | P0 | ✅ | `llm_calls.tick` and `task_label` written; C1 computable |
| EVAL-04 | P1 | ❌ | two `task_label` implementations still disagree, both still orphaned in `stakes_report.py` |
| EVAL-05 | P1 | ⚠️ | `label_calls` has a caller; the six `stakes_report` readers still do not drive the report |
| EVAL-06 | P1 | ❌ | the L1 harness still measures a prompt no resident is given |
| EVAL-07 | P1 | ❌ | L1 results still not persisted; `eval_metrics` unused |
| EVAL-08 | P1 | ❌ | L1 still covers 14 of 77 tools |
| EVAL-09 | P1 | ❌ | judges 3.2/3.3 and P1–P3 still have no driver |
| EVAL-10 | P2 | ❌ | `eval_s4.py` still passes on absence |
| EVAL-11 | P2 | ❌ | G1 and B1 still ignore `addressed_agent_ids` |
| EVAL-12 | P2 | ❌ | G3 still rewards what B3/B4 penalise |
| EVAL-13 | P2 | ❌ | gates that cannot fail before a day boundary — and no run has reached one |
| EVAL-14 | P2 | ⚠️ | the report now scopes itself to this run from the manifest's `started_at` (run 24 reported 1,211 tool calls that were actually 81); the live/trace split was not done |
| EVAL-15 | P2 | ❌ | the control bakery is still built and unwired |
| EVAL-16 | P2 | ❌ | the cast is still all `ablation_arm: full` |
| EVAL-17 | P2 | ❌ | `broker/app.py:335` unchanged |
| EVAL-18 | P2 | ❌ | the geography chain still untested without the gitignored assets |
| EVAL-19 | P2 | ❌ | `engine/tests/conftest.py:129` unchanged |
| EVAL-20 | P2 | ❌ | Parquet export still needs the network and has no caller |
| EVAL-21 | P3 | ✅ | source-text wiring tests converted |
| EVAL-22 | P3 | ❌ | the series was never closed |
| EVAL-23 | P2 | ❌ | no end-of-run harvest of engine minds |
| EVAL-24 | P2 | ✅ | **not stamped** — `run_manifest.json` (CORE-24) |
| EVAL-25 | P2 | ❌ | still no `tool_invocations` table in the trace |
| EVAL-26 | P3 | ❌ | `docs/RUN_REPORT.md` is still 4.5 MB of stale generated output |
| EVAL-27 | P2 | ⚠️ | the suite is **3,207 passed · 0 failed**; the four named failures are gone. Seven `createdb` errors remain, which is INFRA-22, not these |
| EVAL-28 | P2 | ✅ | **not stamped** — `test_sim_agent_adapter.py:118-140` updated to the honest-drain shape; the sim seam is green |

---

# Doc 18 — Growth, construction and expansion (GROW)

**7 ✅ · 0 ⚠️ · 23 ❌ of 30** — all four building locks are off; nothing built is
walkable.

| Id | P | ✔ | Evidence |
|---|---|:--:|---|
| GROW-01 | P0 | ✅ | `grant_plot` writes `world.plots[plot_id]` with `permitted=True` (`institutions.py:179-184`) |
| GROW-02 | P0 | ✅ | `issue_builder_license` as a GOVERNMENT tool (`:213`, registered `:791`) |
| GROW-03 | P0 | ✅ | `_a_finished_building_provides()` (`place_aliases.py:226`) — a completed smithy satisfies the `smithy` gate through its plot |
| GROW-04 | P0 | ✅ | `buildings_due_for_completion` + `complete_construction` driven every tick (`run_genesis.py:5823-5842`) |
| GROW-05 | P0 | ❌ | `wiring.py:1456` still calls `build_world` without `world_map=`; two `WorldMap` instances |
| GROW-06 | P0 | ❌ | `self.map_service = None` (`:3284`); land approval is still a logged no-op. **The single largest open item** |
| GROW-07 | P1 | ❌ | the expanded map is still not checkpointed |
| GROW-08 | P1 | ❌ | two bills of materials with different constants |
| GROW-09 | P1 | ❌ | four disconnected notions of land |
| GROW-10 | P1 | ❌ | the genesis construction company is still a business record and nothing more |
| GROW-11 | P1 | ❌ | tenders, permits and contracts still have no tool surface |
| GROW-12 | P1 | ✅ | **not stamped** — seats now fill (GOV-01), so `petition` has an in-world entry point at last |
| GROW-13 | P1 | ❌ | expansion still ignores the petition and grows bare ground |
| GROW-14 | P1 | ❌ | the viewer still never sees a grown map or a built structure |
| GROW-15 | P2 | ❌ | built sectors still classified inactive on restore |
| GROW-16 | P2 | ✅ | **not stamped** — `is_real_destination` resolves buildings through the plot, not the missing `Building.location_id`; the dead branch is gone |
| GROW-17 | P2 | ❌ | `build_structure` still has `location_gate=None` |
| GROW-18 | P2 | ❌ | construction is still a countdown, not labour |
| GROW-19 | P2 | ❌ | `petitions` and `buildings` still declared and never written |
| GROW-20 | P2 | ❌ | `TileOwnership.from_expansion` still never set |
| GROW-21 | P2 | ❌ | `MapService.from_kasukabe_genesis` still duplicates `build_kasukabe_genesis_map` |
| GROW-22 | P2 | ❌ | `loader.py`, `compute_bill_of_materials`, `save_tiled_map`, `append_columns` still dead |
| GROW-23 | P2 | ❌ | processed goods still have no sink; tools do not wear |
| GROW-24 | P2 | ❌ | `regenerate_zones` still not called by `GenesisRunner.run_tick` |
| GROW-25 | P2 | ❌ | the console still sees only land petitions and amendments |
| GROW-26 | P3 | ❌ | `LandExpansion` payload is still a receipt, not a map delta |
| GROW-27 | P3 | ❌ | map tests still fail rather than skip when assets are absent |
| GROW-28 | P3 | ✅ | a workshop a business provides is no longer listed as its own door (`places.py:158`) |
| GROW-29 | P3 | ❌ | `WORKPLACE_BY_OCCUPATION` still sends builders to `carpentry`, a phantom (`:234`) |
| GROW-30 | P3 | ❌ | stale docstrings still claim the opposite of the code |

---

# Work docs 00 to 09 imply that no finding tracks

Docs 10 to 18 route their work through finding ids. Docs 00 to 09 do not,
and eight items in them are backed by no finding at all - which is why they
appear in no register, no progress board and no count, including the tables
above. All eight were absent from the tree.

## New scope, specified here for the first time

| What | Where | State |
|---|---|---|
| Goals, needs and beliefs | doc 04, in full | No `goals.py`, no `goal_reflection.py`, no model, no tools. `ENG-24` is a one-line stub; doc 04 is the spec. **Blocked on operator decision 10**, still open |
| The policy engine (`PolicyBook`, `set_policy`) | doc 06 section 4.7, E9 | Nothing. The "ideology alters the mechanics" target |
| Ideology measurement | E11 | Nothing |
| Need signals, `town_needs` | doc 05 section 4.1, G11 | Nothing |
| Zoning as policy | doc 05 section 4.5, G13 | Nothing |
| Rulesets; temple as a RELIGION organisation | I7 | `OrganizationKind.RELIGION` exists as an enum value and nothing else |
| Replay server, run registry, tick scrubber | doc 16 section 5, R9 | Nothing |
| The `no_goals` arm | R8 | Blocked on doc 04 |

## Fixed in this pass

Each proven to fail before the fix was kept.

| Doc 01 item | Fix | Guard |
|---|---|---|
| Blocker 8 - the brief taught a word the world refuses | The standing brief said "N units of food. `eat` consumes it" directly above the real holdings list, and `eat('food')` is refused - so a resident doing exactly what its brief said was punished, four times in run 23. `standing_brief` now carries `food_items` and the line names them | `test_the_brief_names_the_food_it_holds.py` |
| Fix 5 - "item names should fuzzy-match" | `errors.nearest_name()`, wired into `NotFoundError` and `RecipeNotFoundError`. It SUGGESTS and never resolves - TOOL-01's own lesson is that a fold which does not reach the storage key moves the refusal one line down | `test_a_refusal_suggests_the_nearest_name.py` |
| Fix 6 - "output cap around 600 tokens" | `SIM_MAX_RESPONSE_TOKENS` 1536 to 768. Not 600: this file's own floor is 768 with a measured reason behind it, and where a round number and a measured floor disagree the floor wins | `test_sim_response_budget.py::test_the_budget_is_a_turn_not_an_essay` |
| EVAL-16, wider than reported | `ablation_summary` was stale in **eleven of fifteen** genesis files, not one - `genesis_3.json` claimed 314 residents. `recount_ablation_summary()` added and called by all four generators; the committed files repaired in place, summary key only | `test_the_genesis_summary_counts_the_cast.py`, `test_every_genesis_generator_recounts_its_arms.py` |
| doc 06 citation | E9 cited `GOV-30`, which does not exist - doc 13 stops at GOV-25. Corrected to `ECON-30`, the policy-lever finding it describes | - |

**A correction on the way through.** Repairing the summaries showed
`genesis_50.json` holds 4 `no_reflection` and 7 `no_learning` residents.
EVAL-16's "no resident in the series cast is in a control arm" is true of
`genesis_20.json` and false of `genesis_50.json`; only the summary was
lying.

**And one of my own.** The first version of the generator fix broke
`make_genesis_neighbours.py`: its `build()` returns a dict literal, so the
recount landed after the `return` as unreachable code referencing an
undefined name, while blanking the summary it was meant to repair.
`importlib.import_module` passed it, because a NameError in dead code fires
at call time and never. `test_every_genesis_generator_recounts_its_arms.py`
calls all four builders and was watched to fail against exactly that state.

**Suite after this pass.** `world/ broker/ supabase/`: **3,259 passed · 7
skipped · 0 failed · 7 errors** in 20 m 58 s. The engine's sim seam: **176
passed · 1 skipped**. Against the 3,207 / 175 baseline recorded above that
is +52 and +1 - exactly the tests added here (5 + 10 + 31 + 6, and one) -
with nothing else moved. The seven errors are unchanged and unrelated:
`createdb` timing out against a dormant Postgres, which is `INFRA-22`.

## Not done, and why

| Item | Reason |
|---|---|
| Cut the genesis runway to 3-5 days (doc 01 fix 1) | It is 26 days today. But it is a policy number of the same kind as `SIM_PER_USD`, it is inert until a run reaches a day boundary (run 24 stopped at tick 80 of 144), and the operator's own note on decision 4 says the re-rating "is the product of both lanes, so it does not land alone". Changing it unilaterally would repeat the mistake that note warns about |
| Accrue upkeep and head tax hourly (doc 01 fix 1) | The highest-leverage item in doc 01 and a multi-part design change: the hunger ladder counts unpaid DAYS, `arrears["upkeep"]` is never written (ECON-15), and the S1 gate asserts at the day boundary. It needs the obligation ledger (E4) underneath it, which does not exist |
| One `day_jobs.py` called from both roots (doc 09 phase 0) | Fights decision 1, which was taken the other way: the runner absorbs the server's wiring and `main.py` is to be retired. One root means deleting the other, not sharing code with it |
| CI carries the Ville assets (doc 09 phase 0) | There is no CI in this repository - no `.github/workflows`. Nothing to change |

## Two the audit got wrong

Both were already built. I searched for the review's phrasing instead of the
mechanism, and this codebase names things after what happened in a run:

- **"Report a turn that ends in prose"** (doc 01 fix 4) is
  `WorldState.silent_turns_report()`, fed by `note_turn()` and rendered
  from `run_genesis.py:4932`.
- **"Make `say` warn when the addressee is absent"** (doc 01 fix 4) is
  `spoke_to_absent` in `say_handler`, which was deliberately changed from
  raising `LocationGateError` so the words still reach the room.

---

# Closed since this document was written

*Appended as the work landed. 37 of the 268 findings, all in docs 14
to 18, each with a guard test that was reverted and observed red before the
fix was kept. The tables above are the audit as it stood on 2026-09-05 and
are deliberately NOT edited in place - a status document that quietly
improves its own past is not a status document.*

| Id | What changed |
|---|---|
| `GROW-06` | `build_the_map()` in `GenesisRunner.boot`; degrades with a warning when the Ville assets are absent |
| `GROW-05` | `build_world(world_map=...)` on BOTH hosts - each had loaded a second WorldMap for MovementSystem |
| `GROW-07` | `MapCheckpointAdapter`, which also re-points MovementSystem after a restore so GROW-05 stays fixed |
| `UI-01` | `map_service` threaded into AdminDeps, AgentsDeps and LocationDeps - all three were hard-coded None |
| `UI-02` | `social_api` and `agent_viewer_api` mounted on the running host |
| `CORE-26` | same mount; the reasoning viewer's data was in the trace store all along |
| `GROW-24` | `regenerate_zones` called from the tick; its only caller was the host no launcher boots |
| `TOOL-20` | same fix |
| `CORE-19` | two routers claimed `/world/state` AND `/places`; the shadowed location_api views renamed |
| `CORE-06` | the life tick built a fresh PopulationDials each tick, so the console's dials reached nothing |
| `ECON-11` | same fix |
| `CORE-07` | `apply_tick_pacing()` moved out of `seed_genesis` into `boot`, so a resumed run is paced |
| `GROW-29` | `resolve_workplace()`; three of twelve workplaces were workshop constants `known_places` never offers |
| `GROW-17` | `build_structure` gated on the plot's location - it was `location_gate=None` |
| `CORE-18` | `last_health_check_at` had NO writer, so the watchdog's silence arm was unreachable code |
| `CORE-32` | the barren halt bounded in seconds as well as ticks |
| `GROW-30` | docstrings the map fix falsified, corrected |
| `CORE-28` | `addressed_agent_ids` read by the report's thread grouping and the activity feed |
| `INFRA-27` | `content_loss` exposed on `GET /admin/trace` |
| `INFRA-14` | the spend cap seeded from the trace store at boot, with `spent_since_boot` kept separate |
| `GROW-26` | `LandExpansion` carries the strip rect `approve_land_petition` already computed |
| `EVAL-04` | two `task_label` implementations deduplicated - and the canonical one's rule 5 fixed against the spec |
| `INFRA-24` | `SupabaseRestSink` and `RateLimiterRegistry` deleted with their tests |
| `INFRA-28` | compose pointed the observatory at a service the file does not define |
| `EVAL-26` | the 4.5 MB stale report and TEST_RUN.md banner-labelled (not deleted - doc 14 cites it as evidence) |
| `CORE-27` | `--world-mode external` refused at validation |
| `GROW-15` | a built sector classified as nothing, so a building the town raised closed itself on the first resume |
| `CORE-21` | `agent.spawned` published at genesis; both LocationIndexes were written by hand and nothing was told |
| `EVAL-10` | `eval_s4` reports N/A rather than PASS when a gate had no input |
| `EVAL-13` | G5 reports NOT EXERCISED below a day boundary; it had passed 20/20 measuring nothing |
| `INFRA-22` | the preflight remedy named a Postgres migration for a SQLite file; migrations/ marked dormant |
| `UI-24` | both transcript clocks moved from IST to UTC |
| `UI-27` | `build_agents.py` deleted - two generators wrote the same agents.js with different keys |
| `UI-07` | the badge branched on a constant, so its SIMULATED SCHEDULE arm was unreachable |
| `UI-19` | the 1.7 MB cast exempted from the per-request module-versioning rewrite |
| `CORE-33` | engine health probes run concurrently; 250 sequential 2s probes could not finish one pass |
| `UI-16` | the run's real spend cap plumbed world -> console; the bar was drawn against a hard-coded $400 |

**Not closed, with reasons.** `GROW-08` waits on operator decision 8 (bill
of materials in processed goods or raw) - `map/parcel.py`'s own comment
says the divergence is deliberate markup, so unifying it would pick the
decision. `INFRA-11`/`EVAL-17` does not reproduce on Windows: `/data`
resolves to `D:\data`, which does not exist, and the import still succeeds.
It needs a Linux host to see, and this project's own bar says a test never
seen red is not evidence.

**Three fixes came from tests, not from the review.** The `getattr` guard
caught two silent defaults I had introduced over real declared fields.
Deduplicating `task_label` revealed that the CANONICAL implementation
deviated from the evaluation spec, not just the copy. And `CORE-19` turned
out to be two collisions, not one - `/places` as well as `/world/state`.

---

# What this adds up to

**The three biggest blocks are done.** Every P0 that stopped the town *acting*
is closed: the resident can remember (`ENG-01`, `ENG-02`), the town can hold an
election and pass an amendment (`GOV-01`, `GOV-03`), somebody can build
something (`GROW-01` to `GROW-04`), the shop has a shelf and a till
(`TOOL-03`), and the record is no longer lying (`EVAL-01`, `EVAL-02`,
`EVAL-03`, `CORE-24`).

**Three P0s are still open and they are the same P0.** `GROW-06` / `UI-01` /
`UI-02` — the production host has no `MapService`. That one omission means map
growth is a no-op, a finished building is placeable by gate but not walkable,
and the DESIGN §8 research viewers 404 on every run. It is the last piece of
decision 1 and the reason phase 0 has not passed.

**`ECON-04` is the fourth.** The treasury holds zero sim at genesis while
residents hold about 1.57 million between them, so everything the government is
now *able* to spend, it cannot *afford*.

**The frontend is untouched.** 30 of 33 UI findings, and the three that closed
are all inside one file.

**Two defects the reviewers missed have since been found and fixed** — the
temple that existed in the world and on no map, and a perception that never
once told a resident where it was standing. The second is worth more than its
size: for 23 runs every location gate was a rule about a fact the resident
could not see.

**Suite at the time of writing:** `world/ broker/ supabase/` — **3,207 passed ·
7 skipped · 0 failed · 7 errors** in 15 m 24 s. All seven errors are `createdb`
timeouts against a dormant Postgres, which is `INFRA-22`. The engine's sim seam
is green.
