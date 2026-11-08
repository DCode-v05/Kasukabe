# Programmatic tool calling: what one model call is allowed to buy

*Written 2026-09-09 alongside PROG-01/02/03. **The decision in this
document has not been made.** It is the operator's, and this file exists
so it is made deliberately and written down rather than settled by
whatever the defaults happened to be.*

---

## The question

`docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 7 states it exactly,
and nothing here improves on the wording:

> **The cost model.** "Thinking costs money" is the experiment. If six
> acts cost one call, a resident that writes good programs is
> structurally richer than one that does not — which is either the most
> interesting result the project could produce, or a confound that makes
> every earlier run incomparable. That is an operator decision, not an
> engineering one.

Both readings are defensible and they lead to different runs.

**If it is the result.** Compounding is the thing this town is named
after. A resident that notices it does the same five things every morning
and turns them into one call has done something no resident has ever done
here, and getting richer for it is the mechanism working, not a bug in
the measurement. Run 25 wrote exactly one skill and licensed nothing to
anybody; the town has never once been observed to compound.

**If it is the confound.** Every earlier run measured turns-with-effect
(G6) and cost-per-resident-day (C1) against a world where one model call
bought at most one act. If that stops being true, run 26 is not
comparable with runs 1 through 25 on either figure, and the change is
invisible in both — G6 goes up because programs act, C1 goes down because
acts got cheaper, and neither number says why.

---

## What has been built, and what it does by default

Three pieces, in the order the audit recommends.

| | | Status |
|---|---|---|
| **PROG-01** | the parent link: `tool_invocations.program_id` / `program_step` | built |
| **PROG-02** | the bounded expression layer: `when`, `repeat`, `bind` | built |
| PROG-02b | per-step charging that survives a partial program | already existed (TOOL-06) |
| **PROG-03** | the measurement: `world/evals/programs.py`, a run-report section, `eval_metrics` rows | built |

The ceilings are world rules, enforced by `world/tools/program.py`, and
every one of them is an operator knob:

| Ceiling | Default | Environment variable |
|---|--:|---|
| steps a program may be filed with | 12 | `KASUKABE_PROGRAM_MAX_STEPS` |
| times one step may repeat | 20 | `KASUKABE_PROGRAM_MAX_REPEAT` |
| dispatches one program may make | 40 | `KASUKABE_PROGRAM_MAX_CALLS` |
| share of the caller's opening wallet it may spend | 50% | `KASUKABE_PROGRAM_WALLET_SHARE_PERCENT` |

**`KASUKABE_PROGRAM_MAX_CALLS` is the lever this decision turns on.** It
is not a safety number like the other three; it is the answer to "how
many acts may one decision buy", expressed as a number.

A value any of these cannot read **fails at boot, naming the variable**.
`KASUKABE_PROGRAM_WALLET_SHARE_PERCENT=5O` (a letter O) falling back to
50% would be an operator who believes they tightened something and did
not, and nobody re-reads a knob. `0` is valid everywhere and is the
strictest setting, not an error.

---

## The three arms

### A. Leave the defaults (one call may buy up to 40 acts)

Programmatic tool calling is on, and run 26 is a different experiment
from run 25. G6 and C1 both move for a reason that is not behavioural,
and the honest thing is to report them as a new series rather than as a
continuation.

*Take this if the question worth answering next is whether the town
compounds at all.*

### B. `KASUKABE_PROGRAM_MAX_CALLS=1`

The mechanism ships, is exercised by the whole test suite, and buys
exactly one act per call — so a program is a composite that stops after
its first step and says so. Run 26 stays comparable with every run before
it, and the feature sits there costing nothing until the question above
is worth spending a run on.

*Take this if run 26 is meant to measure the trade and prompt work from
run 25 without a second variable in it.*

### C. A middle number, and say what it is

Any value between. The reason to pick one deliberately rather than
inherit 40: whatever it is becomes the ceiling on the acts-per-decision
figure PROG-03 reports, and a figure whose ceiling nobody chose is a
figure nobody can interpret.

---

## What PROG-03 will tell you either way

`uv run python -m world.evals programs <trace.db> --record-into <trace.db>`,
and a **What one decision bought** section in every run report.

* **P1 acts per decision** — the headline. 1.0 is a town making every
  call by hand. *Reported, never scored: a bar here would be the code
  answering this document's question.*
* **P2 adoption** — residents who ran a program, composed tools that took
  up control flow at all. A P1 of 6.0 over one program by one resident is
  not a finding.
* **P3 the ceilings held** — **the one gate.** No program exceeded the
  call ceiling; no program left its caller unable to act. Not a matter of
  taste: if that happened the run is contaminated whatever else it shows.
* **P4 the confound** — median sim of residents who wrote a program
  against those who did not, with the question attached and no score.
* **P5 what it cost to find out** — programs against model calls made.

---

## The one thing that is not open

Whatever the number is, **the ceilings are world rules and not
conventions**. The audit's sentence — *"an unbounded loop is a resident
that spends its whole wallet in one turn, so the cap must be a world
rule, not a convention"* — is enforced in `world/tools/program.py` and
held by `app/world/tools/tests/test_a_program_stops_at_its_ceiling.py`.
Setting a ceiling higher is an operator's call. Removing one is not on
offer, because a program that can empty a wallet inside one turn takes
the run's behaviour with it, and no figure in P1 through P5 survives that.

---

## Decision

**Arm chosen:** **A — leave the defaults.** `KASUKABE_PROGRAM_MAX_CALLS=40`,
`KASUKABE_PROGRAM_MAX_STEPS=12`, `KASUKABE_PROGRAM_MAX_REPEAT=20`,
`KASUKABE_PROGRAM_WALLET_SHARE_PERCENT=50`, set explicitly in run 26's
launch environment rather than inherited.

**By:** Sharan (operator) **On:** 2026-09-10, before run 26 booted

**Because:** run 26 is a test run of programmatic tool calling itself. Arm B
stops every program after its first step, so it would ship the feature and
test nothing about it. Run 26 is therefore a **new series** on G6 and C1 and
is not read as a continuation of run 25 — and it would not have been
comparable anyway: it runs `genesis_6.json` (6 residents, not 20) on a
$5.00 cap (not $20.00). P1 is reported against a ceiling of 40 that was
chosen, not inherited.

*Fill this in before run 26 boots. `SPEND_CAP_USD` disagreeing with what
the broker actually enforced is in run 25's own defect list; a
configuration nobody wrote down is the same defect one layer up.*

---

## Addendum, 2026-09-10 — why Arm A measured nothing, and `do_in_order`

**Runs 26, 27 and 28 ran on Arm A and made 0 programs in 662 resident
turns**, so P1 was undefined three times and this decision was never
actually tested.

The residents were not failing to think in routines. They narrated them
(*"walk, harvest what I can carry, walk back, stock the shelf"* — run 26;
*"fell, craft, sell, repeat"* — run 28). What stood between the sentence and
a program was a detour: a program existed only as a tool filed with
`toolify` — a name, a description, a parameter schema — and called on a
later decision. "Later" was literal, because the engine rebuilds its tool
list at the start of a tick. One resident filed one in three runs and never
called it. Run 28 meanwhile spent 45% of its turn calls in turns that ran to
the 12-call limit, one act per call.

**`do_in_order`** (`app/world/tools/meta.py`) runs a program now, in one
call, with nothing filed. It is not a second runtime: the same `ToolStep`
shape, the same `validate_steps`, the same `program.run_program` under the
**same four ceilings this decision set**, every step still gated, charged
and traced, the wrapper filed as step 0 so PROG-03 counts it. It is served
in full to every resident (`ALWAYS_IN_FULL`) — served as an index line it
arrived as an 85-byte schema with no `when`, `repeat` or `bind`.

**What this changes about the decision: nothing, and that is the point.**
Arm A still governs, and 40 acts per call is still the ceiling. What changes
is that a run on this code can finally produce the P1 figure Arm A was
chosen to measure. A run that does will be the first in which one model call
buys more than one act in practice, so its G6 and C1 are not comparable with
runs 26–28 either — say so in that run's report.
