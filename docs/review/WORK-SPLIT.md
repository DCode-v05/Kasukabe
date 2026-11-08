# Splitting the 268 findings: Sharan and Deni

*Companion to `FINDINGS-REGISTER.md` (which finding, which document, which
line, what status) and `../REVIEW_ACTION_PLAN.md` (why the phases are in
that order). This document answers one question only: **who does what, in
what order, and where do the two of us have to agree.***

**Board view:** https://claude.ai/code/artifact/d200cbd5-1c3b-4923-9541-46a293c42e09

## Where both lanes stand — 2026-09-04

**52 of 268 findings closed, 29 of the 99 open questions built, and all four
blocking decisions taken *and* built.**

**The four decisions are Sharan's, and all four have landed:**

| | Decision | Status |
|---|---|---|
| 1 | The runner absorbs the server's wiring | ✅ `CORE-02` + `GOV-04` |
| 2 | History bounded in the seam | ✅ `ENG-02` |
| 3 | `SIM_PER_USD` reduced to 5,000 | ✅ `ECON-05` |
| 4 | A veto window shall exist | ✅ `GOV-03`/`GOV-13` — Deni built it |

Detail under *Sharan's lane*.

| Lane | Closed | P0s closed | Of |
|---|---|---|---|
| **Sharan — the machine** | **27 findings** + 10 questions | 17 | 118 findings, 16 P0 |
| **Deni — the world** | **25 findings** | 9 | 150 findings, 17 P0 |

Two of Sharan's fourteen (`GOV-04`, `ECON-05`) sit in Deni's lane but were
built by Sharan, because decisions 1 and 3 reached into it.

Every FIXED row on both sides was shown to fail before the change was kept:
guard written, fix reverted, failure observed, then restored. Suite on the
merged tree: **3,147 passed, 9 skipped, 0 failed** across `world/`,
`broker/` and `supabase/`; **145 passed** in the engine's sim seam.

**The four decisions, landed.** (1) The runner absorbs the server's wiring —
charter sink and crime engine now exist on the host that runs. (2) History
is bounded in the seam — the old 12,500 window made *every* call in all 23
runs an emergency compaction, because `RESERVE_FLOOR` alone is 25,000.
(3) `SIM_PER_USD` is 5,000. (4) The President is a veto window.

**The 99 open questions across the 19 docs are answered** in
`OPEN-QUESTIONS-ANSWERED.md` - and **answered is not fixed**, so the count
that matters is: **29** settled *and built* (19 at first, plus 10 built
since), **46** still a position with no code, **23** still yours.

## The cut

**Sharan owns the machine. Deni owns the world.**

That is not a convenience split, it follows the shape of the findings. Every
finding is about either *how a turn happens, what it costs and what gets
recorded* — or *what exists in the town to do, what it costs in-world, who
governs it and what you can see*. There is almost no overlap, which is why
this can be run in parallel.

| | Sharan | Deni |
|---|---|---|
| **Owns** | The machine: seam, runner, record, money pipe, measurement | The world: tools, economy, institutions, growth, viewer |
| **Docs** | `10`, `14`, `15`, `17` | `11`, `12`, `13`, `16`, `18` |
| **Families** | ENG, CORE, INFRA, EVAL | TOOL, ECON, GOV, GROW, UI |
| **Findings** | **118** | **150** |
| **P0s** | 5 headed + 11 in the §6 lists = **16** | **17** |
| **The question you answer** | "Did it happen, what did it cost, and can we prove it?" | "Was there anything worth doing, and could anyone do it?" |

Why this way round: I have been inside `run_genesis.py`, `run_report.py`,
`wiring.py`, the four `engine/src/sim/` seam files and the L1 harness all
session — that is exactly ENG/CORE/INFRA/EVAL. Deni wrote `stakes_report.py`
and has been in the world tools, the economy and the institutions — exactly
TOOL/ECON/GOV/GROW.

---

## Neither lane is blocked any more

**This section used to say Sharan was blocked on decision 1** — which of the
two composition roots survives — and that Deni could start immediately while
that was settled.

Both are resolved. All four decisions were taken on 2026-09-04 and all four
are built (see *Sharan's lane* below). The runner absorbs the server's
wiring; history is bounded in the seam; `SIM_PER_USD` is 5,000; the President
is a veto window.

What remains is work, not waiting. The one thing that could still block
either of us is the AWS lane capacity (`15`.1): every throughput number in
DESIGN §9/§11 and the whole 250-resident plan assumes 600 RPM, and the
config records this account as having 200 in one region. That is a look at a
console, not a decision.

---

## Where Deni's lane stands

**D1, D2, and the first cut of D3 and D5 are done, with proof.** Twenty
findings closed, four partly:

- **D1/D1b** TOOL-01, 08, 11, 19; partly 12, 25
- **D2** TOOL-02, 03, 04, 09, 13, 14, 15; GROW-28
- **D0** UI-03, 09, 17
- **D3a** ECON-01, GOV-06; partly GOV-18
- **D5a** GOV-02, GOV-16, TOOL-22; partly GOV-15

Seven of the seventeen P0s on this side. Every one was shown to fail
before the fix was kept. The D2 gate passes: a resident bakes in her own
bakery, shelves the bread, prices it under the id the world prints, a
stranger buys it, and the owner draws the takings - all through
`registry.execute`.

Run 23's four largest refusal classes cannot recur: `no business with id`,
`location:mill`, `you hold N not M`, and `'world_mine' is not something
you can keep doing`. What that buys in a live run is not yet known; no run
has been made against these changes.

Two files this touched are shared with Sharan's lane and are merge points
for S1: `run_genesis.py` (six lines seeding `provides_workshops`) and
`registry.py` (charter refusal classification, the world-aware gate check).

Still open on this side: the rest of **D3** (ECON-04, the treasury that
spends nothing; ECON-13/14/15, the obligation ledger; ECON-17/18/20 - the
re-rating still waits on Sharan's pacing work per handshake 4), **D4**
(construction - GROW-01/02/04 unblocked, the map half waits on S1), the
rest of **D5** (GOV-01's election tick job and GOV-03's amendment tools,
now that the operator has settled the veto window), and the large half of
**D6**.

## Deni's lane: the six blocks, and where they stand

Each block is chosen so the next one has something to stand on. Every item
cites its finding id — the argument and the reviewer's proposed fix are at
the line the register gives.

### D1. One vocabulary for places (TOOL-01, P0) — first, because six other things need it
Five spellings of every place and no resolver at the tool boundary. Until a
tool can turn "the mill", "Kasukabe Mill", `mill_location()` and whatever a
resident invents into one id, every location gate is a coin flip, and my
perception text and report are describing places that may or may not be the
same place. **Ship a resolver at the registry boundary, not in each tool.**

### D2. Things to do with a day (TOOL-02, TOOL-03, TOOL-04, all P0)
- A business owner cannot stock a shelf or draw from a till. Two tools.
- Residents cannot see what they hold. One tool.
- Workshops are ids with no ground; the mill business is not a mill.
  *Note: I checked TOOL-02 and it is not clean-cut — `kitchen_location()`
  does exist (`tools/locations.py:55`) and `cook` has a gate
  (`production.py:113`). The review's claim is that nothing on the map
  satisfies them. Confirm which before rewriting anything.*

Run 23 measured `cook` **0 of 2** and `buy` **1 of 9**. This block is why.

### D3. The economy stops being free (ECON-01 P0, ECON-04 P0, ECON-05 P0)
- Head tax bills 0 sim because the genesis rate is 0 — and the gate that
  checks it counts *calls*, not *sim*, so a zero bill passes. Fix both: a
  non-zero genesis rate, and a gate that asserts money moved.
- Every sim sink lands in a treasury that spends nothing. No payroll, no
  pensions, no relief.
- At 10,000 sim/dollar, energy cannot be bought from honest work. **This one
  is decision 3 below — do not pick a number alone.**

### D4. Someone can build something (GROW-01 to GROW-04, all P0)
Four findings, one story: nothing can create a `Plot`, nothing issues a
builder's licence, `complete_construction` **has zero callers** (I checked —
`construction.py:157`, and one of its own docstrings describes a caller that
was never written), and a finished smithy satisfies no gate anyway. Do them
as one piece or none of them pays.

### D5. Institutions that run (GOV-01, GOV-02, GOV-03, all P0)
The genesis election is never tallied, so **every government tool has been
uncallable in all 23 runs**. No amendment can be tabled or voted. And a
theft case is filed against the victim — the justice tools convict the
robbed. GOV-02 is a bug with a blast radius; do it first of the three.

### D6. The viewer stops lying (UI-03, UI-04)
The township page runs a 3,000-line browser simulation *over* the real run
and can hide living residents. Anything the two of us conclude from looking
at that page is currently unreliable.

**Deni's gate:** a mock run where a resident stocks a shelf, another buys
from it, the seller pays head tax at the day boundary, and the treasury
balance changes. All four in one trace.

---

## Sharan's lane

### The four decisions — taken by Sharan, 2026-09-04 ✅

These were the four blocking decisions the review left with the operator.
All four are now taken **and** built. They sit here because they are
Sharan's calls; where the code landed in Deni's lane it says so.

| | Decision | Built | Where it landed |
|---|---|---|---|
| **1** | **The runner absorbs** the server's wiring | ✅ | `CORE-02` charter sink + `GOV-04` crime engine, both in `GenesisRunner.boot`. `bind_event_bus` proved redundant — `persist()` already does all six of its writes, with a *bounded* `record_event`. `GOV-04` is in Deni's lane; Sharan built it. |
| **2** | **History bounded in the seam** | ✅ | `ENG-02`. `DISABLE_AUTO_COMPACT=1`, the window raised 12,500 → 131,072, and a new `engine/src/sim/bounded_history.py`. Nothing in the frozen `engine_core/` touched. |
| **3** | **`SIM_PER_USD` reduced to 5,000** | ✅ | `ECON-05`. `REVIVAL_COST_SIM` is now *derived* from the rate rather than written, so the next re-rating cannot break an import. Nine rate-pinned tests rewritten to assert the property. In Deni's lane; Sharan built it. |
| **4** | **A veto window shall exist** | ✅ | `GOV-03` / `GOV-13`. `close_the_veto_window` runs on the tick path; `--veto-window-ticks` sets the wait, 0 auto-signs immediately, negative disables auto-signing. **Built by Deni**, verified by Sharan. |

Why decision 1 mattered most: there were two composition roots, and every
run ever made used the one with none of the wiring.

| Wiring | `WorldServer` (what `main.py` builds) | `GenesisRunner` (what **every run** used) |
|---|---|---|
| `rewire_charter_event_sink` | 1 | **0** |
| `bind_event_bus` | 1 | **0** |
| `crime_engine` | 2 | **0** |
| `map_service` | 6 | 3 |

Counted by hand, not taken from the review. Four separate findings, one
cause, one fix.

### The five work blocks, and where they stand

| | Block | Status |
|---|---|---|
| **S1** | One composition root | ✅ `CORE-02`, `GOV-04`. The remaining piece is the map service. |
| **S2** | The record becomes true | ✅ `EVAL-01`, `EVAL-02`, `EVAL-03`, `INFRA-01`, `INFRA-02`. Open: `CORE-03`'s six tables with no writer, and `tool_invocations` has no table at all — found while wiring C1. |
| **S3** | A day that is affordable | ✅ `CORE-01`, `CORE-05`, `CORE-17`, `INFRA-03`. The town can now reach a night, and a night costs almost nothing. |
| **S4** | The resident can actually think | ✅ `ENG-01`, `ENG-02`, `ENG-03`, `ENG-16`. The learning centre writes rows, history is bounded in the seam, and the tool payload went from ~14.8K tokens to ~7.6K. Open: `ENG-04`'s quadratic signal growth. |
| **S5** | Tests that can fail | ✅ `CORE-23`, `ENG-26`, `EVAL-21`. Four source-text files rewritten to exercise real behaviour, plus three new assertions on what actually reaches the wire. |

### The ten open questions Sharan built

Beyond the findings: `17`.5 (a night) · `11`.4 (invented tools on a routine) ·
`11`.7 (hiring asks) · `11`.8 (theft reaches a shelf) · `12`.6 (the richest
member pays) · `12`.4 (upkeep at midnight) · `14`.7 (a lost row blocks a run) ·
`15`.7 (migrations frozen) · `03`.6 (the Law 1 manifest) · `15`.8 (the run
artifact layout).

### The lesson this lane keeps re-learning

**Three times** a test passed with its fix reverted, because it exercised the
helper and not the call site — `INFRA-02` was caught at *zero* call sites
after already going green. Every predicate added here now carries an AST
check on its caller.

**Sharan's gate:** the same mock run as Deni's, queried afterwards from the
trace alone — every event present in `events`, every `llm_calls` row carrying
a tick, the drain fired before the halt, and a night that costs nothing.

---

## Where we have to agree (four handshakes)

These are the only places the split touches. Each needs one conversation, not
a meeting series.

1. **The event name list.** Deni emits, I record and report. EVAL-01
   happened because those two sets drifted with nothing checking. The guard
   test now catches it — but only for names, not payload *keys*, and two keys
   were wrong too (`amount_sim` vs `amount`). **Agree the payload shape when
   you add an event, or extend the guard to keys.**
2. **The place resolver (TOOL-01).** Deni builds it; my perception text and
   report both consume it. I need the resolved id in the record, not the
   resident's spelling.
3. **The one root (S1).** I build it; it *delivers* Deni's crime engine,
   charter sink and map service. When it lands, Deni re-checks that those
   three subsystems actually light up — I cannot verify that from my side.
4. **The shape of a day vs the price of a day.** Pacing and night are mine
   (CORE-01/17); action costs and the exchange rate are Deni's (ECON-05). A
   day the resident can afford is the product of both, so the number lands
   together or not at all.

---

## What still needs the operator

The four blocking decisions are taken and built — they are recorded under
*Sharan's lane* above. What is left needing an answer is smaller and is
collected in `OPEN-QUESTIONS-ANSWERED.md`: of the 99 questions the 19
documents carried, **29 are built**, **46 are a stated default with no code
yet**, and **23 still need the operator**.

Three of those 23 are worth answering before the others:

1. **`15`.1 — the AWS lane capacity.** One look at a console. Every cost and
   throughput number downstream is currently an assumption.
2. **`16`.3 — one map or two.** Keeping both means every construction
   feature is built twice, silently. It is the largest scope question left.
3. **`17`.7 — the canonical cast.** The ladder (10/50/250 at $2) or the
   series cast (20 at $6)? The docs give two answers; the run manifest needs
   one.

And the standing one, which is not ours: **the AWS keys pasted in chat still
need rotating.**

---

## What is already true, and worth not breaking

The dispatcher with its lock/snapshot/refund, the structurally enforced
$1,000 invariant, the metering broker, speech that lands, and persona
delivery to the wire are all sound. The reviewers' own verdict is that *the
residents are already better than the world they live in*. The 150 findings
on Deni's side are mostly "this does not exist yet"; the 118 on mine are
mostly "this exists but nothing calls it". Both are the cheap kind of
missing. **None of the 268 requires touching the frozen engine core.**
