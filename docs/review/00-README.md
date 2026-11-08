# Codebase review: from a town that talks to a town that runs itself

*Written 2026-09-03 against `Real` at `5400e94`, after run 23. Nine
subsystem reviews were made by reading the code end to end (about 120,000
lines of Python and 20,000 of JavaScript), running the test suites, and
checking every claim against the run reports. Every finding cites
`path:line`. Nothing in the code was changed; this directory is the
deliverable.*

## The target this review measures against

A town that runs entirely on its own and thrives: every resident does its
job, earns, eats, sleeps, learns and gets better; individuals, organisations
and the government all function through tools; the town expands when its
government approves and its own builders construct buildings and
infrastructure from materials it produces; residents invent skills and tools
that do more with fewer model calls; prices, wages and rents emerge from
supply and demand, and an ideology change alters the mechanics; every
resident has evolving goals shaped by needs, beliefs and experience; and
the whole thing is a research instrument whose every claim traces to a row.

## The verdict in five sentences

The residents are already better than the world they live in. The
dispatcher, ledger, broker, speech and persona delivery are sound, and the
charter, crime, life, map and construction packages are complete as
libraries. Almost nothing that would make the town run is driven by the
tick loop, and much of what is driven runs on the wrong host, at a zero
rate, into an in-memory database, or against a phantom place. The
resident's memory cannot write a row on the sim path and every prompt is
emergency-compacted, so no compounding has ever had a chance. Every fix
identified here lives in the seam, the world, the registry, the broker or
the viewer; none requires touching the frozen engine core.

## How to read this directory

Read in this order. The specs (03 to 09) are the plan; the reviews (10 to
18) are the evidence.

| Doc | What it is |
|---|---|
| `01-run-23-evaluation.md` | Run 23 measured against a real day: the eight things that kept a morning from being a day, and seven fixes in leverage order. |
| `02-state-of-the-system.md` | Designed, built, wired, proven for every mechanism, and the four causes behind the pattern. The per-clause matrix and the docs-drift table are in doc 17 §4 and §7. |
| `03-spec-an-affordable-complete-day.md` | Pacing, night, memory, prompt size, scaffolding, Kimi markup, asynchronous turns, perception. The precondition for everything else. |
| `04-spec-evolving-goals.md` | Needs, goals and beliefs as world state, deterministic triggers, sleep reflection in the resident's voice, tracing. |
| `05-spec-town-growth-and-construction.md` | Need signals, tenders and contracts, the construction site as a place, private builds, land and zoning, materials sinks, one map. |
| `06-spec-economy-market-and-ideology.md` | Day jobs, obligation ledger, treasury spending, currency re-rating, shelf and till, a clearing market, the policy engine that turns ideology into mechanics. |
| `07-spec-institutions-that-run.md` | Election tick job, amendments with a veto window, budget, organisations with members and rules, justice with ground truth, temple. |
| `08-spec-research-instrumentation.md` | The run artifact, every table written, the evaluation suite by area with sources, the viewer as a window, test-suite health. |
| `09-roadmap-and-acceptance.md` | Seven phases with gates, and the fourteen decisions that block them. |
| `10-engine-seam-and-mind.md` | ENG-01 to ENG-27: the resident's mind, tick end to end, learning, compaction, scaffolding, goals design. |
| `11-world-tools-and-physics.md` | TOOL-01 to TOOL-30: the 77 tools, place ids, workshops, inventory, movement, invented tools, construction gates. |
| `12-economy-and-life.md` | ECON-01 to ECON-32: currencies, upkeep, tax, action costs, wages, debts, life, genesis balance with numbers. |
| `13-governance-organisations-justice.md` | GOV-01 to GOV-25: elections, powers, policy engine design, organisations, land approval, crime, temple, checkpoints. |
| `14-world-core-runner-and-record.md` | CORE-01 to CORE-33: the run path, pacing, scheduling, perception text, trace, checkpoint, supervision, observability. |
| `15-broker-trace-and-infra.md` | INFRA-01 to INFRA-30: the money pipe, trace schema, throughput, translate, deployment, reproducibility, tests. |
| `16-frontend-and-console.md` | UI-01 to UI-33: pages, the two maps, DESIGN §8 viewers, the console, live mode, the in-browser simulation, research UI. |
| `17-evals-tests-and-docs.md` | EVAL-01 to EVAL-28: L1 to L4, the report's sources, the designed/built/wired/proven matrix, docs drift, suite health. |
| `18-growth-construction-expansion.md` | GROW-01 to GROW-30: whether anyone can build anything, the expansion pipeline, the full construction design, materials. |

## The twenty findings that matter most

Each is P0 or the P1 that most blocks the target. Ids point into the
review documents.

1. Two world servers; every run uses the one the roadmap said to retire, so
   map growth, politics events, the crime engine and the research viewers
   are absent from every run (CORE §2, GROW-06, GOV-04, UI-02).
2. The learning centre cannot write a single row on the sim path; nothing
   consolidates, souls never move, and batches re-send the whole history at
   55K to 108K tokens (ENG-01).
3. Every model call is emergency-compacted; the day-0 perception is pinned
   into every prompt and tool results are gutted (ENG-02).
4. All ~79 tool schemas go on every call and the location-reduced ones never
   reach the wire (ENG-03, ENG-16).
5. Nobody can build anything: no plot, no licence, no completion caller,
   and a completed smithy would satisfy no gate (GROW-01 to GROW-04).
6. Land approval grows no map on the production host; even on the other
   host movement uses a second, stale map (GROW-05, GROW-06, UI-01).
7. The genesis election is never tallied, so every government tool has been
   uncallable in all 23 runs; no amendment can be tabled or voted (GOV-01,
   GOV-03).
8. A theft case is filed against the victim; the built justice pipeline is
   never constructed on the running host (GOV-02, GOV-05).
9. Head tax is charged at zero and the gate passes; upkeep is charged at
   the first tick as a transfer the report cannot see (ECON-01, ECON-02,
   CORE-04).
10. Every sim sink lands in a treasury that spends nothing; no payroll,
    pensions, tenders or relief (ECON-04, GOV-08).
11. At 10,000 sim per dollar and ~$2 per agent-day, energy is unbuyable
    from honest work (ECON-05).
12. Place ids have five vocabularies and no resolver; workshops are strings
    with no ground; the mill business is not a mill; there is no kitchen
    (TOOL-01, TOOL-02).
13. A business owner cannot stock the shelf or draw from the till; residents
    cannot see what they hold (TOOL-03, TOOL-04).
14. Ticks are unpaced, night is dispatched, the drain never fires, and
    dormant residents are refused every third tick (CORE-01, CORE-05,
    CORE-17, INFRA-03).
15. Paid turns are discarded at 120 s while the engine keeps running and
    billing them (CORE-11, INFRA-29).
16. Checkpoint snapshots go to an in-memory database; call rows carry no
    tick; world events are not recorded; six tables have no writer; there
    is no manifest (INFRA-01, INFRA-02, INFRA-16, INFRA-17, INFRA-09).
17. The run report reads event names the world never emits, hence "Nobody
    posted a price" over twelve posts (EVAL-01, TOOL-16, CORE-03).
18. Kimi's text-form tool calls are parsed by nothing (ENG-08, INFRA-07).
19. The township page runs a 3,000-line browser simulation over the real
    run and can hide living residents (UI-03, UI-04).
20. The tests on exactly the paths that are broken assert source text and
    cannot fail (CORE-23, ECON-26, GOV-24, ENG-26, EVAL-21).

## What is new relative to the design

Two things the operator asked for are not in DESIGN.md and are specified
here for the first time: evolving goals, needs and beliefs (doc 04), and
the policy engine that lets an ideology change alter the mechanics (doc 06
§4.7, doc 07). Both are built on existing pieces and stay inside ROADMAP's
three laws.

## Decisions the operator owns

Fourteen decisions block the plan; they are collected in doc 09 and each
spec's "Decisions" section. The four that matter first: one composition
root; history bounded by the seam; the currency re-rating; and whether the
President becomes a veto window so an unattended run can ever grow.

## Method and limits

Each subsystem was read completely by one reviewer against the shared brief
in this directory's history, with the run 23 report as ground truth. Test
suites were run where they finish in minutes: the app suite is 3,145 passed,
110 failed, 68 skipped, with 117 of the failures and skips due to the
gitignored map assets being absent from this checkout, 9 due to a network
fetch, and three or four needing triage (doc 17 appendix). The engine's
sim-seam subset is 116 passed, 1 failed on a stale test. Where a reviewer
inferred rather than read, the finding says so. The reviewers did not run
the town.
