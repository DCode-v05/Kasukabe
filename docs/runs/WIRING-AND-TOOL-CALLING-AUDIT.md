# Wiring, tool calling, and what is actually holding

*2026-09-09, against `HEAD`. Every claim below was run, not recalled;
where something was checked and found already correct, it says so.*

---

## Seven things to say, in order

*Plain language, no jargon, each with the "so what". Enough for two
people to walk a room through this without reading the rest.*

**1. The town couldn't sell a loaf of bread — and it wasn't the AI's
fault.** Fifty residents, 351 rounds, $27.78 of model calls. **Zero
completed sales**, four runs running. The assumption was that the agents
weren't smart enough to trade. They were. A shopkeeper put bread on her
shelf, set a price of 30, and two customers walked in with money. Both
were told *"she has no bread."* The price had gone on **her**; the bread
was on the **shelf**.
→ *Before blaming the model, check whether the system let it succeed.*

**2. One word did it, and the system said "success".** She typed
`business_id`. The tool wanted `on_behalf_of_business`. The system
**silently threw the word away** and reported the price posted — which
it had, on the wrong thing. An unrecognised word is now a refusal that
names what the tool actually takes.
→ *A silent "yes" is worse than a loud "no".*

**3. Half the money bought turns where nothing happened.** 3,341 of
3,741 turns ended with the model thinking and calling nothing. 595 of
those *wrote out* the actions they'd decided on, as prose, and never made
the calls — one described calling a tool that does not exist. We took
OpenAI Codex's rule for long-running agents and translated it; the
load-bearing half is *"nobody is waiting to finish the job for you."*
→ *An agent that has decided is not an agent that has acted.*

**4. Three connections carry every run, and each has broken silently
before.** The tool has to change the world; the model has to see the
tools; what happened has to get recorded. Every one has failed, and the
run looked healthy from outside every time — speech once went into a
queue nobody read, politics into a dead end for 23 runs. All three are
now checked on a really-booted town, with a test that keeps them true.
→ *The dangerous failures are the ones that don't raise an error.*

**5. 8% of actions never arrived — and nothing knew.** 97 calls died in
transit. The world couldn't count them: it *was* the thing that was
unreachable. Only findable by grepping logs after the run ended, which
is how it survived four runs. We added counters — **then found nothing
read them.** Three things read it now.
→ *Adding the measurement is half the job. Someone has to look at it.*

**6. One fix was tried, broke two things, and was reverted.** One call
in six was the same question twice. Folding those looked obvious — until
two existing tests caught it: repeating "rest" is how an agent gets
better at its trade, and re-arriving where you already are is how the
map learns where you are. Reverted, and written into the code so the
next person doesn't repeat it.
→ *The tests earned their keep. Reporting the revert is the point.*

**7. None of it has run against a live model yet.** Every fix is proven
by test, including an end-to-end sale replaying the exact morning that
failed. But tests are not a town. **Two numbers on the next run tell
us:** G4 — did anything get sold — and G6 — what share of turns changed
something. They were **0** and **23%**.
→ *Don't claim it's fixed until the run says so.*

---

## The short answer

**"Wired correctly · verified"** means the path was already built right —
nothing was broken and nothing needed repairing. It was checked on a
really-booted town, and there is now a test that keeps it true. It does
not mean "on hold". (It read "Holding" in the first draft, which was
ambiguous in exactly the wrong direction for a status column.)

| # | Asked | Status |
|---|---|---|
| 1 | Artifact issues fixed and pushed | **Done** — 7/7 in the register, verified string by string |
| 2 | `tool → world` wired | **Wired correctly · verified** — a test boots a town and follows the call |
| 3 | `tool → sim` wired | **Wired correctly · verified** — 42/42 index rows usable here name their parameters |
| 4 | `tool → output` wired | **Wired correctly · verified** — nothing recordable is published and dropped |
| 5 | Kimi K2.5 tool calling | **Protocol is fine.** The failures were parameter *shape*, not calling |
| 6 | Engine visibility | **Was a real gap** — the link had no instrument at all. Now it does |
| 7 | Spread test | **Added** — deterministic, every tool, every commit |
| 8 | Evaluations | **Existed** (`verb_reach`) — the two gaps are now filled, 52 scenarios |
| 9 | Programmatic tool calling | **Built** - PROG-01/02/03; the cost model stays the operator's — §7 |
| 10 | Prompts correct + locations | **Inventoried, all 14 verified** — §6 |

---

## 1. The three wires

Three paths carry every run. Each has been silently broken at some point
in this project, and **every time the run looked healthy from outside**:
speech published into a bus nobody recorded (RC-2); politics into
`noop_sink` for 23 runs (CORE-02); index rows telling a model a tool took
no parameters (TRADE-02). None of them raised. Each produced a run that
cost money and proved nothing.

**All three were already wired correctly.** Nothing in this section was
repaired. What changed is that each path is now *verified on a booted
town* and held there by a test, so the next silent break is caught by a
red suite instead of by a run that cost money and proved nothing.

`app/world/tests/test_the_three_wires.py` (**WIRE-01**) boots a real
town, drives one call down each path, and looks for it at the far end.

```
── A. tool -> world ──
  [PASS] move changes location_of() or starts a journey
  [PASS] say is charged against the ledger
  [PASS] say reaches world.utterances

── B. tool -> sim ──
  [PASS] registry rows are produced                    79 rows
  [PASS] rows split into full schemas and index lines  22 full / 57 index
  [PASS] index rows usable here name their params      42/42
  [PASS] every full row has a JSON schema

── C. tool -> output ──
  [PASS] events table has rows
  [PASS] the utterance reached the utterances table
  [PASS] transactions table has rows
  [PASS] tool_invocations recorded in world
  [PASS] published vs recorded is measurable
  [PASS] no recordable event was published and dropped
```

**It is end to end on purpose.** What breaks is never the piece; it is
the seam between two pieces that each still pass their own tests.

### Two errors it caught in itself

Worth stating plainly, because they are the same error the project keeps
making and I made it twice more while writing the test for it:

- it looked for a `required` key on index rows, when the parameters live
  in the **description** as `Needs: goal_id.` — so it reported TRADE-02
  unwired when TRADE-02 was fine;
- it looked for speech in `events`, when speech has its own `utterances`
  table.

Both are EVAL-01 — a reader keyed to names nothing writes — committed by
the test written to catch it. Recorded in the file.

---

## 2. Kimi K2.5 tool calling

**The calling protocol is not the problem.** Measured on the run's own
logs: 6,460 HTTP 200 against 3 HTTP 500, and only **2 turns of 3,741**
carried raw `<|tool_call_*|>` markup — so the model was returning proper
`toolUse` blocks and the INFRA-07/ENG-08 repair path was not the
bottleneck.

What actually failed was **the shape of the arguments**, and that is a
wiring problem, not a model problem:

| Cause | Evidence | Status |
|---|---|---|
| A parameter the tool did not have was **silently dropped**, returning success | `post_price(business_id=…)` posted on the person; 2 buyers refused | **TRADE-01 fixed** |
| Index rows advertised `"properties": {}` | `harvest({})` 20 of 38 calls | **TRADE-02 fixed** |
| Bare-name markup with no `<|tool_call_begin|>` | 3 spans in the report parsed to nothing | **ENG-08 fixed** |
| 4 × CONTENT LOSS — billed 1 output token, every block empty | `broker.log` | Detected, logged, emits `broker.content.lost` |
| **200 RPM, not 600** | first line of every broker log | **Deployment config, not code** |

That last one is worth reading twice. `app/broker/lanes.py` refuses to
build a lane with no inference profile, because a bare model id is an
`AccessDenied` waiting to happen mid-run — and it says so loudly. Four
of six lanes have no profile. **That is an AWS configuration action, and
no code change will fix it.**

---

## 3. Engine visibility — fixed, in two passes

Startup readiness was already wired: `wait_for_engines_ready` polls
`GET /health`, times out non-fatally, and logs the decision.

**Mid-run, there was nothing.** Run `deni-a6f9eb4-5h` lost 97 calls to
transport — 8% of every tool call the town made — and *nothing in the
running system knew*. The world could not count them; it was the thing
that was unreachable. The runner had no counter. No endpoint reported
them. They were findable only by grepping session logs after the run was
over, which is how they survived four runs.

**WIRE-02, first pass:** `WorldClient` counts `attempted`, `refused`,
`lost` and `recovered`, and serves them on the engine's `GET /health`.

**And that was only half a fix.** Nothing read them. The runner polls
that endpoint once, at startup, and looks only at the status code — so
the instrument existed and no consumer did. That is ENG-16 exactly: *a
fix that reaches nobody is indistinguishable from no fix*, a mistake
this project has shipped before. It was caught by being asked whether
the gap was actually closed. It was not.

**WIRE-02, second pass — three consumers, each answering a different
question:**

| Consumer | Answers |
|---|---|
| `GET /world/link` | *Is it happening right now?* 97 lost calls would have been worth knowing at tick 40, not at the end. Returns `lost_share` and `above_alarm`. |
| The shutdown line | *Did it happen at all?* At or above **1%** it is an **ERROR** — a lost call is indistinguishable from a refusal to the resident that made it, so the run's behaviour is contaminated, not merely slow. |
| `runner.wire_health` | *What were the numbers?* Collected **before** teardown — a stopped engine cannot answer, and this is the one figure a run cannot reconstruct afterwards from anything it wrote down. |

`recovered` is the one to watch: it separates "the link is fine" from
"the link is bad and the retry is carrying us". A run losing nothing
only because every call is retried still gets a warning, because that
will not carry it forever.

---

## 4. The spread test

Two layers, and they answer different questions.

**`app/world/evals/verb_reach.py`** — the live one, ~6¢ a run. Puts a
real model in the exact situation a verb exists for and asks whether it
reaches for it. 21 scenarios. Commerce coverage: `post_price` 5,
`trade` 7, `buy` 4, `sell` 4, `transfer` 2, `give` 1 — and
`stock_shelf` and `take_from_shelf` had **none** — both appear in the
failing sale — and now have two each, including a negative and an
after-refusal. **52 scenarios.**

**`app/world/tests/test_every_verb_can_be_called_from_what_it_shows.py`**
(**WIRE-03**) — the deterministic half, added here. No model, no money,
every tool, every commit. For each one: the schema is well formed, a
call built *from that schema* validates, the reduced index row names
what a resident must supply, and the town has not grown a third word for
"quantity".

Its first draft produced **four false findings** — `find_people`,
`register_org`, `come_of_age`, `build_structure` "refusing calls built
from their own schema". Every one was my generator ignoring a constraint
the schema does carry: a `minLength`, two enums, a `minItems`, then
nested objects and tuple `prefixItems`. **The schemas were honest; the
generator was shallow.** That is written into the helper, because a
false finding from a test written to catch false confidence is exactly
the one to remember.

---

## 5. Evaluations that exist

| Surface | File | What it asks |
|---|---|---|
| Verb reach (L1) | `app/world/evals/verb_reach.py` | does a model reach for the verb in its own situation |
| Behavioural | `app/world/evals/behavioural.py` | run-level behaviour gates |
| Character | `app/world/evals/character.py` | does a resident stay itself |
| Answered/deflected | `app/world/evals/answered_or_deflected.py` | is a question answered |
| Judges | `app/world/evals/judges.py` | the LLM-judge harness |
| Gates | `gates.txt` per run | G1–G6, the run's own pass/fail |

**G4 (settlements) and G6 (turns with effect) are the two that failed**,
and are the two the trade and prompt work targets.

---

## 6. Every prompt, and where it lives

All fourteen verified to exist at the symbol named, with line numbers as
of `9354268` — **the line will drift, the symbol will not**, so grep the
symbol if the number has moved. **`_index_row` has a leading
underscore**; an earlier draft of this table said `index_row` and was
wrong.

### Two folders, and the split matters

```
the world's side — what a resident is told and shown
  app/world/life/soul_seed.py        the law, the persona, the broadcast
  app/world/retrieval_depth.py       the perception, rebuilt every tick
  app/world/run_genesis.py           the standing brief, the roster
  app/world/tools/primitives/*.py    19 files — every tool description
  app/world/tools/registry.py        the reduced index row
  app/world/gap_feedback.py          the gap notice

the engine's side — what is written back into memory
  engine/src/sim/resident_voice.py   the post-turn summary
  engine/src/sim/session_log.py      the turn record, fed back next turn
  engine/src/sim/goal_reflection.py  the sleep reflection
```

| Surface | File | Symbol | Line |
|---|---|---|---|
| **The law** — cached, every turn | `app/world/life/soul_seed.py` | `law_of_the_town_rules()` | 163 |
| The persona — written once at boot | `app/world/life/soul_seed.py` | `build_persona_prompt()` | 353 |
| The soul seed — wraps the law | `app/world/life/soul_seed.py` | `build_soul_seed()` | 323 |
| The periodic broadcast | `app/world/life/soul_seed.py` | `default_broadcast_message()` | 406 |
| The town roster | `app/world/run_genesis.py` | `town_roster_text()` | 5305 |
| **The perception** — rebuilt every tick | `app/world/retrieval_depth.py` | `render_perception_text()` | 1204 |
| — the closing line | `app/world/retrieval_depth.py` | last `sections.append` | 1472 |
| — the standing brief | `app/world/run_genesis.py` | `standing_brief()` | 5177 |
| — goals / needs / beliefs | `app/world/retrieval_depth.py` | `format_goals()` | 572 |
| **Tool descriptions** — 19 files | `app/world/tools/primitives/*.py` | each `build_specs()` | — |
| — the reduced index row | `app/world/tools/registry.py` | `_index_row()` | 369 |
| The post-turn summary | `engine/src/sim/resident_voice.py` | `resident_summary_prompt()` | 87 |
| The turn record — fed back | `engine/src/sim/session_log.py` | `render_action_record()` | 154 |
| — when nothing was called | `engine/src/sim/session_log.py` | `SILENT_TURN_RECORD` | 122 |
| The sleep reflection | `engine/src/sim/goal_reflection.py` | `REFLECTION_SYSTEM` | 62 |
| The gap notice | `app/world/gap_feedback.py` | `render_notice()` | 133 |

### What was changed, and the line it does not cross

The persistence rule is **folded into the existing speech rule**, not
added beside it — the law is a cached section on every turn of every
resident and its nine-rule cap is right:

> Nobody prompts you again inside a turn and nothing you concluded is
> carried out for you: the turn ends when you stop calling tools, and a
> decision you did not call simply does not happen. A tool's answer is
> the only account of what occurred — anything you write about having
> acted that did not come back from a call is something you imagined,
> and you will read it next turn as memory.

That is Codex's base instruction translated. Its agents are told to
*"keep going until the query is completely resolved, before ending your
turn and yielding back to the user"*, and the load-bearing half is that
**nobody is waiting to finish the job**. A resident has no user to yield
back to at all.

**Nothing added tells a resident to act, to trade, or to speak.**
DESIGN.md §4: *"told the prices and the rules, never the strategy."*
Every addition states a mechanism that was already true of the engine
and had simply never been said.
`test_a_turn_that_decides_is_told_it_must_act.py` holds that line with a
list of coaching phrases that must not appear.

---

## 7. Programmatic tool calling — built

**Built 2026-09-09, in the order this section recommended.** What was
scoped here is now three findings — PROG-01, PROG-02, PROG-03 — and the
one thing this section said was not an engineering decision is still not
one: it is in `docs/decisions/PROG-COST-MODEL.md` with an unfilled
decision block.

### Why it matters here more than in most systems

Kasukabe's whole economy is *thinking costs money*. A resident spends one
model call per turn and gets one decision. In run `deni-a6f9eb4-5h` that
bought 400 acting turns out of 3,741, at $27.78. If a resident could write
a small program — *walk to the mill, take 5 flour, walk to the shop, stock
the shelf, post 30* — that is one model call for six acts instead of six
calls, and the ratio the project measures changes shape.

### The three things, and what each turned out to be

**(3) A trace that shows the program, not just its calls — PROG-01.**
Done first, as recommended, and for the reason given: it is cheap, it
changes no behaviour, and without it no run can measure whether any of
this helped. `tool_invocations` now carries `program_id` and
`program_step`, through the record, the trace table, the
`/world/tool_invocations` route and the report. **The wrapper is step 0**,
so a program is one `group by` and acts-per-decision is a subtraction
rather than a join. `NULL` on an ordinary call, deliberately: if every
call carried a program id the ratio would be 1 by construction and the
measure would say nothing.

**(1) A bounded expression layer — PROG-02.** `world/tools/program.py`.
Three additions to a step and no more: `when` (one comparison drawn from
a closed set named in `models.py`), `repeat` (clamped, not refused), and
`bind` (a name a later step reads back as `@name` or `@name.field`).

The restriction that does the work is not any of the ceilings. It is
this: **a condition can only see what a step already paid to find out.**
There is no way to read world state from inside a `when`. A resident that
wants to branch on whether the shelf is empty puts a real `look_around`
in the program and reads its binding — a gated, priced, refusable, logged
call like every other. A condition with its own window onto the town
would be a second way to read it that nothing charges for, and the whole
argument that a composite is safe (`meta.py`, three numbered guarantees)
is that it adds no path the registry does not already own.

Four ceilings, every one an operator knob, every one enforced by the
runtime rather than trusted to the author — *"the cap must be a world
rule, not a convention"*:

| Ceiling | Default | Answers |
|---|--:|---|
| steps filed | 12 | how long a program may be, checked before it can be taught or sold |
| repeats per step | 20 | how many times one step may run |
| dispatches per program | 40 | the total, once loops expand — the per-step cap does not imply it |
| share of opening wallet | 50% | **the one this section actually named.** The others bound the shape; only this bounds the damage |

**Stopping is not failing.** A program that meets a ceiling keeps every
act it completed, says which ceiling and at which step, and does not
raise — because `execute` restores tools-owned state on any exception,
and *"you harvested nine and then we un-harvested them"* is a worse answer
than *"you harvested nine and stopped."* A step the world REFUSES still
raises, and TOOL-06's refund still gives back what the earlier steps paid.

**(2) Per-step charging that survives a partial program.** Was already
done, as this section said. Re-pinned against the new execution path by
`test_a_plain_composite_still_runs_as_a_plain_list.py`, so a runtime that
quietly charged for its own bookkeeping is caught by a red suite rather
than by a run report.

### One path, not two

Every composite goes through the runtime, including one filed before
programs existed — a plain composite is a program whose every step is
unconditional, unrepeated and unbound. Routing the old shape around it
would have meant the commonest shape in any run was the one PROG-01 never
measured, and two paths that must agree is the seam section 1 of this
document is about.

### What it found on the way in

- The `toolify` row in `action_costs.SCHEDULE_TABLE` still described the
  surcharge TOOL-06 removed, and cost disclosure appends that row to the
  served blurb — so the text a resident reads before composing anything
  said composing makes the work dearer. The guard that should have caught
  it searched for a number and could not see the digitless spelling.
- A repeated read inside a program is folded by TRADE-04 and leaves no
  row in the log, so counting it as an act would have put the wrapper's
  own count permanently ahead of what a report can see. Counted
  separately; the ceiling bounds dispatches, the economics report acts.
- `test_no_silent_defaults` fired on a three-argument `getattr` in the
  trace writer. Fourth time. Removed rather than the budget raised.

### The measurement — PROG-03

`uv run python -m world.evals programs <trace.db> --record-into <trace.db>`,
plus a **What one decision bought** section in every run report and rows
in `eval_metrics`. Three consumers, wired the day it was written, for the
reason section 3 of this document exists.

**P3 (the ceilings held) is the only gate.** P1 (acts per decision) and
P4 (do program writers end richer) are reported and never scored, because
scoring them would answer the operator's question by asserting it.

### Still not an engineering decision

> "Thinking costs money" is the experiment. If six acts cost one call, a
> resident that writes good programs is structurally richer than one that
> does not — which is either the most interesting result the project could
> produce, or a confound that makes every earlier run incomparable.

Unchanged, and unanswered here. `KASUKABE_PROGRAM_MAX_CALLS=1` reduces a
program to a single act and keeps run 26 comparable with every run before
it, at the cost of the feature. The three arms and the empty decision
block are in `docs/decisions/PROG-COST-MODEL.md`. **`keep_doing` still
overlaps this** and extending it may still be the cheaper half; nothing
built here forecloses that.

## 8. The "still not fixed" list, worked through

Five items. **Two fixed, one does not exist, one is an AWS action, one
needs a run.**

### Fixed — TRADE-06, `move` repeats (64 of the 199)

A journey of zero tiles is now free. The difference from the first
attempt is *where* it applies: that one **folded** the call — short-
circuited before the handler — which dropped `agent.moved` (the event
carrying the tile coordinates perception and the map read) and left a
result claiming "nothing was charged" while dispatch had already
charged. Two regressions, both caught by tests that were right.

This one answers only a pricing question, the shape TOOL-06 already uses
for an invented tool's wrapper. The handler still runs, the event still
publishes.

**`rest` (61 of the 199) is deliberately still charged**, and there is
now a test saying so. It looks idempotent and is not: `keep_doing`
repeats it through this same dispatch precisely so each repetition earns
proficiency and is charged. Folding it silently disabled that feature
last time.

### Fixed — TRADE-07, the dead listing

The deadlock that cost the run every transaction, in its last surviving
form. TRADE-01 fixed the case where the resident *named* the business
and the name was dropped. This is the commoner one: it names nothing. A
resident does `stock_shelf`, the goods leave its hands for the shelf,
then `post_price` with no business — so the ask went on the person, who
now holds none.

The ask now lands on the shop, but **only when all four hold**, so it
can never quietly sell somebody else's stock: the resident holds none of
the item, it owns or works for *exactly one* business here, that
business holds some, and it is standing on the premises. Two shops in
one place is left alone — guessing which till gets paid is worse than
posting on the person and letting the note say so.

### Does not exist — the memory verbs

Folding the three engine-side memory verbs was on the list. Measured
against the run: **four calls in 3,741 turns** — `search_memory` 2,
`recall_memories` 1, `recall_session_log` 1 — and **zero repeats**.
There is nothing to fold. Recorded so the item stops being carried
forward as unfixed when there is nothing to fix.

### Fixed — the missing scenarios

`stock_shelf` and `take_from_shelf` had none, and both are verbs in the
morning that settled nothing. Four new ones, including a negative (a
harness that only scores positives measures eagerness, not judgement)
and an after-refusal replaying the exact `take_from_shelf({})` message
five residents hit, now that it names what the tool takes. **52
scenarios.**

### Still open, and neither is a commit

- **200 RPM.** Four of six lanes have no inference profile. `lanes.py`
  refuses to build a lane that would `AccessDenied` mid-run and says so
  on the first line of every broker log. **AWS console.**
- **None of it has run against a live model.** Every fix is proven by
  test, including an end-to-end sale that replays the failing morning.
  **G4 and G6 on the next run are the only real verdict.**

---

## Verification

**app 4,634 passing** · **engine seam 255** · **broker 461** · **52 eval scenarios** ·
12 skipped (host preconditions, INFRA-30).

Every fix follows the standing method — write the guard, revert the fix,
watch it fail, restore. The reversions run for this batch: blanking the
`Needs:` clause reddens the index assertion; making a params model reject
its own advertised schema reddens the contract assertion; removing the
retry, or retrying refusals, reddens the transport tests.
