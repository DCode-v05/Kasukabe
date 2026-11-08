# The run series: ten diagnostic runs, then six on the fixed build

**What this is.** A record of sixteen runs of the town, in two halves.
The first ten were diagnostic: each was run, graded against the six
gates, read, and whatever it exposed was fixed before the next one. The
second half was meant to be ten on one unchanged build and **stopped at
six** - four graded, one disputed, one that persisted nothing. The reason
it stopped is in section 5 and the result is in section 6.

The title said "then ten on the fixed build" and sections 5 and 6 read
"(pending)" long after those six had run and been graded (EVAL-22). Both
are now what happened rather than what was planned.

**The discipline.** Build Spec v2 §10.1 forbids grading a mid-run sample
as a result, and this project has made that mistake three times. Every
number below is read off a **completed** trace store by `app/loop_report.py`,
after the world has stopped. §10.4 forbids moving a threshold after seeing
a result, and **no threshold moved during the series**.

Each run keeps its own directory under `docs/runs/series/<name>/`: the
broker log, the runner log, the gate report, and every engine's session
log, so any figure here can be traced back to the turn that produced it.

---

## §1 The gates

| # | Measure | Bar |
|---|---|---|
| G1 | Reply rate to directed remarks | ≥ 60% |
| G2 | Exchanges reaching 4+ alternating turns | ≥ 3 |
| G3 | Distinct speakers in one place, one window | ≥ 3, at least once |
| G4 | A price agreed in speech, then moved by a tool call | ≥ 1 |
| G5 | Agents alive at stop | 10 / 10 |
| G6 | Cognition turns producing a world effect | ≥ 50% |

---

## §2 The ten diagnostic runs

| run | calls | failed | spend | utts | directed | G1 reply | G2 deep | G3 groups | G4 settled | G5 alive | G6 calls | per turn |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|:-:|--:|--:|
| short1 | 203 | 0 | $0.81 | 18 | 13 | 31% | 2 | 3 | 0 | 10/10 | 42% | 37% |
| run2 | 225 | 0 | $1.11 | 21 | 12 | 25% | 1 | 1 | 1 | 10/10 | 79% | 53% |
| run3 | 206 | 1 | $1.04 | 36 | 31 | 45% | 2 | 2 | 2 | 10/10 | 69% | 52% |
| run4 | 488 | 0 | $2.88 | 41 | 28 | 36% | 3 | 3 | 1 | 10/10 | 65% | 32% |
| run5 | 533 | 0 | $3.24 | 31 | 25 | 44% | 1 | 1 | 0 | 10/10 | 74% | 36% |
| run6 | 476 | 0 | $3.09 | 39 | 31 | 45% | 3 | 2 | 0 | 10/10 | 75% | 41% |
| run7 | 369 | 0 | $2.19 | 36 | 26 | 38% | 2 | 2 | 0 | 10/10 | 73% | 44% |
| run8 | 370 | 0 | $2.12 | 50 | 34 | 44% | 3 | 2 | 0 | 10/10 | 74% | 46% |
| run9 | 394 | 0 | $2.14 | 40 | 34 | 53% | 2 | 2 | 0 | 10/10 | 69% | 38% |
| run10 | 284 | 0 | $1.45 | 21 | 19 | 37% | 2 | 2 | 0 | 10/10 | 59% | 41% |

**3,548 calls, one failure, $20.09.** The single failure is run3's orphan
`tool_result`, fixed in §3.6 and not seen since.

**A change of initial conditions at run10.** The shop-stock fix (§3.9)
landed between run9 and run10, so runs 1–9 ran in a town where every shop
was empty and run10 did not. Runs either side of that line are **not
comparable on G4**, and this is stated rather than buried.

---

## §3 What the runs found, and what was done about it

Nine defects. Each was found by reading a run rather than by reading the
code, and each fix has a test that fails without it.

### 3.1 63% of turns touched nothing, and one resident never acted at all

`short1`: 26 of 71 agent turns produced a tool call. Not spread evenly —
n01, n07 and n08 acted on 4 turns of 7; **n06 acted on 1 of 8**, and what
he wrote instead, three turns running, with `actions: []`:

> *Wren takes his time answering. He looks at each of them in turn —
> Marta, Idris, Corwin, Liora.* "I haven't eaten either. And the field's
> waiting." … "What do you need from me before I walk to the strip?"

Marta was standing beside him. She was asked a direct question. She never
heard it, because none of it happened.

Two rules already said exactly this — law rule 6 in the soul seed, and the
turn-close line ending every perception — and both were in his prompt.
Neither is **evidence**. They are rules stated in advance, and a model that
believes it complied does not re-examine a rule it thinks it followed.

**Fixed:** `WorldState.silent_turns_report` — one line, past tense, after
two consecutive turns that touched nothing: your last N turns reached
nobody and changed nothing. It names no action and no addressee. One quiet
turn is never mentioned; standing and thinking is a thing a person does.

**Effect: G6 42% → 79%.**

### 3.2 A correction I owe the record

Before measuring, I said G6 was probably flattering the town by counting
model **calls** rather than turns. Measured per turn it is 37% against
42% per call — slightly worse, not better. The series report now prints
both columns.

### 3.3 Every run spent its first nine minutes watching the town sleep

`run2`'s utterances all fall in ticks 36–43 and it ended at 44. `short1`'s
in 36–56, ended at 56. Tick 36 is not a coincidence: `TICK_MINUTES` is 10
and `DAY_START_HOUR` is 6, so **06:00 is tick 36**, and a run starts at
midnight with every resident asleep until dawn.

At 15 seconds a tick that is **nine minutes of wall clock**. Run 2 was a
nine-minute run. Its whole conversation happened in the last hundred
seconds and was then graded on how many questions had been answered — and
the scheduler wakes each resident every three ticks, so a question asked at
tick 40 could not have been answered before 43.

**G1 and G2 had never once been measured on a town with time to hold a
conversation.**

**Fixed:** `--start-at-dawn`. Off by default — the night is real and the
learning pipeline runs in it — and passed by the series harness. Neither
bar moved; what changed is that a run now *contains* the thing the bar is
about.

**Effect: utterances 21 → 36, G1 25% → 45%.**

### 3.4 A hypothesis the measurement refuted

I suspected G1 was dragged down by greetings counted as questions.
Restricted to remarks that actually ask something, run2 is **0 of 8**
rather than 3 of 12. The town does not answer, and G1 is a fair gate.
Recorded because I had said the opposite.

### 3.5 The harness killed run 2 twenty seconds in and graded the corpse

`pgrep` does not exist in this Git Bash and returns non-zero for any
query, so `! pgrep -f world.run_genesis` was true on the first poll. The
run was killed with nine calls to its name and `loop_report.py` dutifully
graded them: G1 0%, G3 FAIL, G6 71%. Every one of those numbers is about
the harness. Liveness is measured by whether the runner log is still
growing.

### 3.6 The orphan `tool_result`

One failure per run, undiagnosed for two of them because the trace stored
the request as an unparseable 64 KB prefix. With the trace fix (§3.8) in
place, run3's failing request reads plainly:

```
[1] user      ['tool_result']
[2] assistant ['text']         <- no tool_use anywhere in it
[3] user      ['tool_result']  <- so this one answers nothing
[4] user      ['text']
[5] user      ['text']
[6] user      ['text']
```

Two wire-format faults, either fatal. **Fixed** in `broker.translate.legalise_transcript`:
an orphan result becomes a text block carrying the same words (never
deleted — a transcript that drops it leaves an agent that harvested three
wheat with no idea whether it worked), and same-role turns are merged.
Orphans are matched on `tool_use_id`, not on count, because one call and
one result about different things pass a count check and are still wrong.

### 3.7 Six residents addressed each other by name and the world heard nobody

`run4`: thirteen remarks went to the room, nine named somebody standing
there, **three** resolved to a listener. The six that did not share one
shape:

> 'Wren Thorne. I have wheat in my hand and words that need saying.'
> 'Bram Holloway. I worked all morning and have nothing.'
> 'Liora Thorne. You were looking for Perrin Holloway this morning.'

The name is its own sentence — which is how a person gets somebody's
attention across a room. `_VOCATIVE_AFTER` held `,;:!?-` and no full stop.

A remark with no addressee can never be answered, never appears in that
person's open questions, and never puts them under any obligation. **G1 sat
between 25% and 45% for four runs while the town was, in plain English,
talking to each other by name.** Replayed against run4, seven of thirteen
now reach a person instead of three.

The full stop is not simply added to the set: `'I saw Greta.'` addresses
nobody. What separates them is position — a vocative full stop opens the
remark or follows another sentence. Five real mentions are pinned as
mentions. The same measurement found the inverse defect, fixed here too:
*"You can go find Perrin, or go home to Nell"* resolved to Perrin, who is
being talked about.

### 3.8 The trace lost 60% of request bodies

342 of 566 successful calls in `loop5.db` stored `_head` — a 64 KB prefix
of the JSON, cut mid-token. That is a string, not JSON, so no reader could
parse six calls in ten, and they were the six that mattered because they
were the big ones. The comment beside the cap claimed *"64 KB holds every
honest prompt this system produces with room to spare"*; the same run's
median request is 76 KB and its largest 154 KB.

**Fixed:** a capped body is now a smaller **valid object** — model, ceiling,
system prompt, tool names and newest messages survive; the middle becomes
one marker counting what went. This is what made §3.6 diagnosable.

### 3.9 Every shop in Kasukabe was empty, in every run ever made

From the residents' own failed calls across runs 5–8:

```
world:buy   "b_bakery needs 2 bread, has 0"           x7
world:sell  "no business with id 'harvey_oak_supply'"  x5
world:give  "you hold 0 stale bread, not 1"
world:trade "you hold 0 bread, not 1"
```

They try to buy, sell, give and trade, and every attempt fails because
there is nothing anywhere. Both genesis files carry `"inventory": null,
"stock": null` on every business; `SmallBusiness` had no field for either;
pydantic dropped them. **Two declared keys read by nothing.**

It explains a settlement gate reading zero on four consecutive runs — not
because the residents will not bargain, but because a bakery with no bread
is not a bakery. Run 6's transcript is six people discovering it one after
another: *"The larder's bare"*, *"Supply store was empty when I went"*,
*"What can a hungry student do to earn bread?"*

**Fixed:** `opening_stock` is read, and the declared shops open with a
morning's worth. Not a subsidy — nobody is given anything; a shop is given
the goods it is declared to sell. Capped, and a test pins the cap.

### 3.10 The line that told residents they need not answer

`run5`: eight turns carried an open question and **all eight produced no
tool call at all** — against 57 of 94 on turns without one. Marta asked
Wren three times, in three separate remarks, and he never answered.

The section ended: *"Answer with `say` if you mean to, or let it go and do
what matters more."* That second half was written to stop the mechanism
forcing a reply — one that forced a reply would score 100% and measure
nothing. But it is the last thing in the section, it is an explicit
invitation to do neither, and it arrives attached to a question somebody
is waiting on.

**Fixed:** it stops at the fact and the verb. Turns holding an open
question went from **0% acting to 50%** in the next run.

---

## §4 What is now known about G1, which is the gate still failing

The plumbing is complete, and this was measured rather than assumed. In
run6, of 17 unanswered directed remarks, **16 reached the listener's own
prompt** — most of them on five to eight separate turns. Perrin saw
Tobias's question on eight turns and never answered it.

So G1 is no longer measuring delivery. It is measuring whether residents
choose to answer, which is the research question. Any further mechanism
that closed the gap would be coercion, and a town that answers because it
was told to would make G1 a measurement of our prompt.

**The bar stays at 60%.**

---

## §5 The final ten runs

**Six were run, not ten, and the series was stopped there.** This section
read "(pending)" for months while the runs sat on disk ungraded, which is
the worst state a record can be in: the work was done and the conclusion
was missing, so anyone reading this file learned nothing from six real
runs and could reasonably assume they had never happened.

Every figure below is copied from each run's own `gates.txt` in
`docs/runs/series/final*/`. Nothing here is recomputed and no threshold
moved.

| run | calls | ok | spend | utts | G1 reply | G2 deep | G3 group | G4 settled | G5 alive | G6 effect | verdict |
|---|--:|--:|--:|--:|--:|--:|--:|--:|:-:|--:|---|
| final1 | 414 | 407 | $2.53 | 61 | 16/50 = 32% | 4 | 4 | 1 | 10/10 | 85% | graded |
| final2 | 457 | 450 | $2.53 | 39 | 13/30 = 43% | 2 | 2 | 2 | 10/10 | 61% | graded |
| final3 | 424 | 424 | $2.37 | 54 | 9/28 = 32% | 2 | 3 | 2 | 10/10 | 79% | graded |
| final4 | 239 | 239 | $1.09 | 34 | 11/30 = 37% | 3 | 3 | 0 | 10/10 | 67% | graded |
| final5 | 299 | 299 | $1.48 | 31 | 13/25 = 52% | 3 | 3 | 0 | 10/10 | 64% | **disputed, not counted** |
| final6 | — | — | — | — | — | — | — | — | — | — | **no data** |

**final5 is excluded, and the reason is not the one written on it.** Its
directory holds both a `VOID.txt` saying *"final5 recorded 0 calls. A run
that did not happen is not a result"* and a `gates.txt` grading 299 calls.
Those cannot both describe one trace store, and the VOID's stated ground
is the one that is demonstrably wrong — the calls are there. What is
unique to final5 is the condition it ran under: its `broker.log` is 334
KB and 4,195 lines with 180 throttle/retry and 510 error lines, against
~27 KB and ≤7 throttles in every other run. It is excluded as **run under
conditions no other run in the cohort shared**, which is a comparability
judgement, not a claim that it did not happen. Its conversations are real
and §5.3 of the consolidated report reads them; its 52% is the best G1 in
the set and is **not** quoted as the series result.

**final6 produced nothing gradeable.** No `sessions/`, a zero-byte
`broker.log`, no `gates.txt`. `runner.log` shows preflight passing 13/14,
engines up, ticks dispatching with some 500s and an `n09` timeout, and
then nothing persisted. It is listed so the cohort is six and not five.

**Why the series stopped at six.** `docs/kasukabewhatismissing.md` §5:
*"do not spend that budget on ten more six-hour mornings. Finish items 1
and 2 first, then run the series on full days."* Cost per agent-day
measured about twenty times the roadmap's ladder budget (§ same doc), and
the remaining four runs would have bought four more mornings of the same
answer. **The decision was taken and never written down here** — that gap
is EVAL-22, and this paragraph is it being closed. The four unrun runs
are not owed; a different, longer series is.

---

## §6 Final result

**Five gates pass. G1 does not, and the series ends without it.**

Across the four graded runs (final1–final4):

| gate | bar | result | |
|---|---|---|:-:|
| G1 reply rate | ≥ 60% | 32%, 43%, 32%, 37% | **FAIL, 0/4** |
| G2 exchanges 4+ turns | ≥ 3 | 4, 2, 2, 3 | pass 2/4 |
| G3 group, 3+ speakers | ≥ 3 once | 4, 2, 3, 3 | pass |
| G4 settlements | ≥ 1 | 1, 2, 2, 0 | pass 3/4 |
| G5 alive at stop | 10/10 | 10/10 every run | **pass 4/4** |
| G6 turns with world effect | ≥ 50% | 85%, 61%, 79%, 67% | **pass 4/4** |

**The one honest sentence.** The town forms multi-party conversations
reliably, nobody dies, and what is said moves the world — but the
residents answer barely a third of what they are asked, and no build in
this series moved that.

§4 established that this is not a delivery failure: in run6, 16 of 17
unanswered directed remarks reached the listener's own prompt, most on
five to eight separate turns. The gap is attention and disposition, not
plumbing, and the bar stayed at 60% rather than being lowered to meet the
result (Build Spec v2 §10.4).

**Two cautions on reading these numbers.**

1. *G1 denominators are small* — 25 to 50 directed remarks per run — so
   a few remarks move the percentage several points. The consistent
   pattern (G1 always short, G3/G5 always clear) is the signal; the
   ordering of runs is not. This project has twice called an improvement
   from three runs and twice retracted it.
2. *The reply-rate figures in this table may themselves be understated.*
   Four separate readers in this codebase counted only
   `listener_agent_id` and ignored `addressed_agent_ids` — CORE-15,
   EVAL-11, UI-12/UI-30 — on a record that carried more than one
   addressee on 172 of run 23's 229 utterances. Those readers are fixed
   now, but **every G1 figure above was produced by the old ones**. They
   are left exactly as recorded, because a status document that quietly
   improves its own past is not a status document. **Do not quote them
   as the current reply rate**: they are what the series measured, and
   what the series measured was measured narrowly. A re-run on the fixed
   readers is the only thing that would settle it.
