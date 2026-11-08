# State of the system: designed, built, wired, proven

*Synthesised from the nine subsystem reviews (docs 10 to 18); the per-clause DESIGN.md and ROADMAP matrix and the docs-drift table are in doc 17 §4 and §7. Status words: **designed** appears in DESIGN.md or ROADMAP.md; **built** exists as code with tests; **wired** is reachable from the production run path (`world.run_genesis --world-mode embedded`, which every launcher uses); **proven** has happened in a live run and is in a trace. A green test suite is not evidence of "wired": the charter, crime, life and map suites all pass on code with no production caller.*

## 1. The one structural fact

There are two world servers and the runs use the one the roadmap said to
retire. `world/main.py` plus `wiring.py` carries the map service, the crime
engine, the charter event sink, the trace event binding, the social and
agent-viewer routers, and no engine routes; `run_genesis.py`'s embedded app
carries the engine routes, the day jobs, and none of the former. Every run
is embedded (`runseries.sh`, `start-genesis.sh`). Consequences appear in
every subsystem below: map growth is a logged no-op, politics events go to a
void, the research viewers 404, the crime pipeline is never constructed,
and the two hosts have different physics (CORE §2, ECON-08, GOV-05, GROW-06,
UI-02). ROADMAP T3 ("one world, one contract") is the precondition for most
of this review.

## 2. The matrix

| Mechanism | Designed | Built | Wired | Proven live | Evidence |
|---|---|---|---|---|---|
| Persona delivered as system prompt (STABLE) | yes | yes | yes | yes | ENG §2 |
| Evolved soul and social graph (DYNAMIC layer) | yes | yes | no: stores never constructed | no | ENG-01 |
| Learning centre consolidation at sleep | yes | yes | no: cannot write a row | no | ENG-01 |
| Engine skill artifacts as cheaper cognition | yes | partly | no | no | ENG-05, ENG-06 |
| Compaction bound to the wallet (T8) | yes | no: window lowered instead | n/a | no | ENG-02 |
| Speech: `say`, addressees, conversations, open questions | yes | yes | yes | yes (229 utterances) | TOOL, CORE-15 |
| Perception: energy line, prices here, who is here | yes | yes | yes | yes | CORE §2 |
| Perception: holdings, obligations, commitments, journey, goals | yes | partly | no: dead branches | no | CORE-16, TOOL-04 |
| Walking on the real map | yes | yes | partly: jump fallback for anyone without a tile | partly | TOOL-05, CORE-31 |
| Canonical place ids | implicit | no | no | no | TOOL-01 |
| Workshops as places (mill, kitchen, sawmill) | yes | as strings only | no | no | TOOL-02, GROW-28 |
| Craft and cook recipes | yes | yes | gated on phantom places | 5 of 23 succeeded | TOOL-02 |
| Posted prices, buy, sell, trade, transfer | yes | yes | yes | yes | TOOL, ECON |
| Business shelf and till for the owner | implicit | no | no | no | TOOL-03 |
| Wages: hire, set, pay, accrue, reconcile | yes | yes | yes | never called | ECON, TOOL |
| Upkeep | yes | yes | yes, at the first tick, as a transfer | yes, invisibly | ECON-02, CORE-04 |
| Head tax | yes | yes | yes, at 0 bps | zero sim moved | ECON-01, GOV-06 |
| Treasury spending: payroll, pensions, tenders, relief | yes | partly | no | no | ECON-04, GOV-08 |
| Energy purchase (`buy_energy`), revival, dormancy, death | yes | yes | yes | never reached | ECON-05, ECON-21, INFRA-03 |
| Obligations and debts as ledger state | implicit | head-tax only | partly | `check_debts` empty | ECON-13 to 16 |
| Illness affecting work; pharmacy treatment | yes | yes | no | no | ECON-06, ECON-12 |
| Inheritance of money; births | yes | yes | no | no | ECON-07, ECON-23, UI-05 |
| Continuing actions (`keep_doing`) | yes | yes | yes | both run-23 calls failed on naming | TOOL-13, TOOL-23 |
| Invented tools (`toolify`) and skills (`skillify`) | yes | yes | yes | one tool, failed on first use | TOOL-06 |
| Tool conveyance (licence, sell, teach) | yes | yes | yes | never called | TOOL |
| Elections: schedule, phases, tally, re-schedule | yes | yes | no: never tallied, never re-scheduled | no | GOV-01 |
| Government tools (tax, petition, police, rule, patents) | yes | yes | unreachable: seats vacant | no | GOV-01, TOOL-08 |
| Amendments by three-fifths | yes | yes | no vote or table tool | no | GOV-03 |
| Policy levers changing mechanics | yes | tax only | tax only | no | GOV §5.4 |
| Organisations with members, money, rules | yes | rows only | no | no | GOV-10, TOOL-30 |
| Rulesets (T6) | yes | no | no | no | GOV |
| Crime: theft, complaint, investigation, warrant, verdict | yes | yes, twice | the wrong one; case against the victim | no | GOV-02, GOV-05 |
| Temple keeper, rites, doctrine | yes | no | no | no | GOV §5.6 |
| Land petitions and approval | yes | yes | operator-only; map grows on the unused host only | no | GOV-11, GROW-06 |
| Map expansion with purpose, ownership, corridors | yes | partly | no | no | GROW-13 |
| Plots, permits, builder licences | yes | yes | no issuing tool | no | GROW-01, GROW-02 |
| Construction: build, crew time, completion, place with gates | yes | partly | no completion caller; name satisfies no gate | no | GROW-03, GROW-04 |
| Tenders, contracts, public works | yes | tenders without money or bids | no | no | GROW-11 |
| Materials economy: processed goods consumed, tool wear, decay | yes | no | no | no | GROW-23 |
| Checkpoint and resume | yes | yes | incomplete: map, hunger, debts, conversations, minds | yes for the parts covered | CORE-08, GROW-07, INFRA-09 |
| Pacing: tick interval, night burst, event wakeups | yes | partly | interval dead; night dispatched | no | CORE-01, CORE-17 |
| Spend governor and pre-halt drain | yes | yes | drain only on the burst path | drain never fired | CORE-05 |
| Broker: lanes, metering, parent debit, purpose routing | yes | yes | yes | yes | INFRA §2 |
| Trace: calls with tick, events, six tables, snapshots, payloads | yes | partly | tick NULL, events unrecorded, snapshots to memory | no | INFRA-01/02/16/17 |
| Run manifest, engine-mind checkpoints, Parquet, Litestream | yes | partly | no | no | INFRA-09, INFRA-10 |
| Viewers: governance, voting, group chats | yes | yes | yes | yes | UI §4 |
| Viewers: reasoning, finder, scenario feed, location detail | yes | yes | not mounted on the running host | no | UI-02 |
| Viewer draws expansion and construction | yes | no | no | no | UI-01, GROW-14 |
| Console: petitions, veto, prayers, dials | yes | yes | yes | yes | UI §4 |
| Console: baby blessing queue | yes | yes | nothing feeds it | no | UI-05 |
| Evaluation: L1 verb reach, L3 judges, L4 arm, control bakery | yes | yes | L1 run; bakery unwired | partly | EVAL, ECON-25 |
| Evolving goals, needs, beliefs | not in DESIGN; operator target | no | no | no | ENG-24, doc 04 |

## 3. The pattern behind the matrix

Four causes account for almost every "built, not wired" row:

1. **Two composition roots** (§1). A mechanism wired on one host is absent on
   the other, and the roadmap's chosen host is not the one that runs.
2. **Engines built as libraries with no tick driver.** Elections, crime,
   construction completion, zone regeneration, pensions, inheritance and
   the learning drain all have a documented intended caller that nobody
   wrote (GOV-01, GOV-05, GROW-04, GROW-24, ECON-07, ECON-09, CORE-05).
3. **Silent defaults and dead branches.** A zero tax rate that passes the
   gate, an in-memory database that logs success, a perception branch whose
   inputs are never produced, a pacing interval nothing reads, a memory
   system with no stores (ECON-01, INFRA-01, CORE-16, CORE-01, ENG-01).
4. **Tests that assert source text.** The exact paths that are broken are
   covered by tests that grep the code for a function name (CORE-23,
   ECON-26, GOV-24, ENG-26).

The fix pattern is the same everywhere: one day-job module and one tick-job
module called from both roots (or one root), behavioural tests on a mock
runner, and a "has a production caller" test per engine.

## 4. What is genuinely good

The dispatcher (`registry.py`) is one path with a lock, snapshot and
restore, charge-before-handler with refund, proficiency, refusal log and
event publish. The ledger's $1000 invariant is structurally enforced. The
broker meters exact usage, routes by inferred purpose, repairs orphan tool
results and streaming content loss, and is well tested. Speech, addressees,
open questions and group conversations work and are instrumented. The
persona reaches the wire, and the law-of-the-town ordering is measured. The
charter, crime, life, map and construction packages are complete as
libraries. The evaluation discipline (gates fixed before runs, no mid-run
samples) is right. None of the fixes in this review require touching the
frozen engine core.
