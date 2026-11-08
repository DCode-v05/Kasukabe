# Kasukabe after twenty runs: what is missing, and what to do next

*An assessment written 2026-09-02 from ROADMAP.md, DESIGN.md, the run 20
report, the run 19 issue list, SERIES.md, HUMAN_BASELINES.md,
SELF_AUTHORING_AGENTS.md, and the `Real` branch on origin (94 commits, all
twenty run reports, and the 50-resident run of 2026-09-02). Every figure
below is read from those documents or from the code on `Real`. Where I
could not verify something I say so.*

---

## 1. The verdict in one paragraph

The residents are already better than the world they live in. The model
holds a three-tier price book across thirty ticks, repays a debt it carried
unspoken for five weeks, and, when it answers a question at all, answers
about as well as a person does. What the town lacks is not intelligence. It
lacks **stakes**, **time**, and **compounding**. Every gap in this document
is on the world side of the roadmap's Law 1: a mechanism that is absent,
or built and never invoked. None of it requires touching the engine core.

---

## 2. Where the project actually is

### 2.1 Every run has been the same six game-hours

The series harness starts at dawn (tick 36, 06:00) and the spend cap ends
the run near noon (tick 72). Twenty runs, one morning each.

Consequences, all of them structural:

| Mechanism | Needs | Has ever happened |
|---|---|---|
| Learning centre (skill artifacts, social updates, soul evolution) | a sleep tick | **No.** `learning_ran` is zero in every session log checked |
| Evolved soul diverging from genesis persona | at least one night | No |
| Tokens per task falling from day 1 to day 5 | five days | No |
| Proficiency rising with repetition | dozens of repetitions | No. Highest count in run 19 was two |
| Authoring (`skillify`, `toolify`) | repetition | No. Zero calls in 20 runs |
| Daily upkeep and head tax | a day boundary | No |

All four of the roadmap's Round 2 compounding quantities are multi-day
quantities. "Nothing was authored" has been reported three times, and each
time it was a statement about run length rather than about the residents.

### 2.2 Why the runs are short

Cost per agent-day is about twenty times what the roadmap's ladder budgets.

| | Run 20 |
|---|---|
| Spend | $10.03 |
| Residents | 20 |
| Ticks covered | 36 (06:00 to 12:00) |
| Spend per agent-tick | $0.014 |
| Spend per agent per game-day (144 ticks) | **$2.00** |
| Genesis wallet per agent | $2.00 |
| Roadmap rung 1 budget per agent-day | $0.10 |
| Mean input tokens per call (240 sampled rows) | 19,400 |
| Largest input | 35,600 |
| Fresh context (system prompt plus 48 tool schemas) | about 8,000 |
| Most model calls by one resident in one tick | 50 |
| Turns per resident, least to most | 19 to 158 |
| Post-turn summariser share of calls (run 19 measurement) | 20% of calls, 10% of spend |

Three drivers are visible in the trace:

1. **Context.** Compaction fires near 30k tokens (threshold 0.12 of a 256k
   window). Every tick pays for the whole morning's history.
2. **Unbounded agentic loop per tick.** One resident's tick can be fifty
   model calls. Two residents with the same brief differ eightfold in cost.
3. **Assistant scaffolding still on the wire.** The `request_plan`
   reminder appeared in 61 of 240 sampled rows. Residents write 1,536-token
   interior monologues, hit the output cap, and are resumed in a second and
   third call. The summariser genre was corrupting memory until
   `resident_voice.py` rewrote its prompt. Under Law 1 these are engine
   findings to write up; the sim seam can suppress them the same way it
   already rewrites the summariser.

At this rate, twenty residents for five game-days costs about $200, and 250
residents for five days about $2,500. The economics end the research before
it starts.

### 2.3 What is working, and should be kept

- Fifty residents, 2,288 calls, zero failures, 50 of 50 spoke, 5 of 6 gates.
- 88 MB per engine, so one machine fits about 177 residents.
- The economy moved in run 20: 16 sales, 11 purchases, 3 transfers, 23
  `find_people` calls for a tool that had never been called before.
- Marta's bakery: retail 30, regulars 20, wholesale 24 by the dozen, a
  queue managed by name, and a distributor asking for the wholesale number
  before naming volume. Nobody was told to bargain.
- The 400-sim debt, carried through a dozen runs, was named, acknowledged
  and paid.
- The live judge. It shows G1 fails from **inattention, not evasion**: of
  fifty addressed residents, 34 never spoke back, 3 deflected, 13 answered.
  Of the 16 who did reply, 81% genuinely answered, inside the human band.
- The Dunbar finding: seven threads exceeded four speakers (11, 10, 8, 7,
  7, 5, 5) and one split. A plausible cause of the reply failure rather
  than a companion to it.
- The discipline: gates fixed before runs, thresholds never moved, every
  fix with a test that fails without it, every figure traceable to a turn.

### 2.4 Repository state

`main` is 90 commits behind `Real`. ROADMAP.md on `main` prescribes tracks
T1 to T8; the series work went to conversation gates G1 to G6 and to
self-authoring. Against the roadmap's tracks:

| Track | State on `Real` |
|---|---|
| T1 Character layering | `ConsciousnessManager` wired. Evolved soul has never had a night to move |
| T2 Speech | Done, and well instrumented |
| T3 One world, one contract | Runs still use `--world-mode embedded`. Trace store does not carry utterances; run 20's live figures were lost on report regeneration |
| T4 World as corrector | Not started. No `SignalStore` writes from the seam. `tool_refusals` is recorded and goes nowhere |
| T5 Tool economy | `toolify`, `skillify`, `revise_*` exist. No licence, sale, grant, teach or inheritance of tools |
| T6 Rulesets | Not started |
| T7 Navigation and observation | Largely done |
| T8 Context bound to wallet | Not done |

---

## 3. What is missing

### 3.1 Stakes: the survival loop has no closing link

The design rests on one loop: energy lets you work, work earns money,
money buys energy. Checked against the 56 registered tools on `Real`, the
last link does not exist.

**No tool buys energy with money.** The ledger has `SIM_PER_USD` and a
credit function. `list_places` tells residents they can "buy energy credit
with sim currency at the government store". No agent-callable tool does
it. Dollar wallets only go down, so nothing a resident does changes how
long it lives. Cognitive poverty, altruism near zero, survival ethics at
$0, and metacognition about the price of thinking are all unanswerable by
construction. The word "energy" appears five times in the whole run 20
report.

**Upkeep and head tax have never been charged.** Both are daily. No run has
crossed a day boundary, and I found no call site for `charge_daily_head_tax`
or the upkeep charge in the run loop. Nobody has ever paid for existing.
Without a tax bill, the tax rate is not a political question and the
complaints desk has nothing to hear.

**No wages.** `hire_employee` grants business tool access and nothing pays
anyone. A student is told "your work is at Oak Hill College" and nothing
there earns. Jobs are labels.

**No posted prices.** `buy` takes the buyer's own `unit_price_sim` and
checks it against nothing. A bakery cannot have a price, only the baker's
speech. Price discovery has nothing to converge to, and a customer can pay
one sim per loaf. I found no price field on the business model.

### 3.2 Time: nothing spans a tick

**Every action is instant except walking.** A day of farming is 23 separate
`harvest` calls, each preceded by a 20k-token deliberation. This is the
root of both the cost problem and the "thinking instead of living" look.
What is missing is a continuing action: commit to farming this strip until
dusk, and be woken only by an event such as being addressed, hunger, or
completion. Routine becomes cheap and interruption becomes expensive,
which is what "energy is cognition" was meant to mean. (`standing_orders`
in the registry is a charter field for procedures of state, not this.)

**No night has ever happened.** See 2.1.

**Intentions do not persist.** Perception carries what others said to you
and what you owe an answer to, but not what you set out to do. Aldous
Vance re-decides to go to college on four consecutive ticks and never goes.
A one-line "what you committed to" section, written by the resident's own
actions, would remove most of that churn.

### 3.3 Compounding: nothing gets cheaper or spreads

**Skills do not reduce cognition.** Mastery saves eight sim on a harvest
(17 falling to 9). The design thesis is that a skill artifact loads into
context and shortens the turn. Combined with continuing actions, a
mastered skill becomes a routine that runs with no model call at all. That
is the moment "mastery lowers the cost of living" is literally true and
measurable in dollars.

**The world never corrects.** Refusals are recorded in `tool_refusals` and
go nowhere. `step11_skill_evolution` still waits for a human correction.

**Nothing can be conveyed.** No licence, sale, grant or teaching of tools,
and no inheritance of them. A tool invented once can never reach a third
resident, so diffusion is zero by construction.

**Authoring by nudge has failed three times.** Hint at eight repetitions,
then three, then the pair rule: zero `skillify` calls. Either publish that
as a negative result about tool-using models, or move authoring where
DESIGN.md originally put it: the learning centre writing skill artifacts
during sleep from the day's tool invocations. A resident also has no
motive to author until a skill saves dollars rather than sim.

### 3.4 Institutions: surfaces without cause

`vote`, `petition`, `pray`, `file_complaint`, `register_org` and the patent
office exist and have never been called in the series. There is no
`arrest`, no police, no court verdict, and no tax to resent. Nothing gives
a government something to do or a voter something to want.

### 3.5 The record

- `utterances` has no addressee column, so the addressee fix is invisible
  to the trace and baseline B2 cannot be measured.
- The world's event bus is not bound to the trace store in genesis mode.
  The run report is the only record of what was said, and run 20's live
  figures were lost when the report was regenerated after exit.
- The roadmap's T3 gate says a run that cannot write a complete trace
  halts. It does not yet.

---

## 4. What it looks like when these are in

Game day three. Wren Thorne has farmed the strip on a continuing action
since dawn at a fraction of a cent, because harvesting is now a skill the
learning centre wrote during his sleep. The tax bill lands at midnight and
he hires Aldous Vance for a posted wage rather than pay it from savings.
Marta Thorne's bakery has a price on the door and Farid undercuts it at the
grocery. Tobias Holloway thought too hard for two days, ran his wallet to
zero, and is dormant. The Holloways are arguing over whether to pay ten
thousand sim to revive him, and Nell wants the town to vote on a relief
fund.

Every sentence of that is impossible today, and not because of the model.

---

## 5. What to do, in order

| Order | Work | Unlocks |
|---|---|---|
| 1 | Continuing actions in the world, plus an energy-purchase tool | Cost falls, stakes appear, one game-day becomes affordable |
| 2 | Per-tick call budget from the wallet, compaction near 10k, location-relevant tool schemas only, summariser thinned or routed | Under $0.20 per agent-day; rung 1 becomes a nightly job |
| 3 | Wire daily upkeep and head tax into the run loop, add wages, add posted prices | A real economy and a reason for politics |
| 4 | The first run that includes a night, then five days | Learning centre, soul drift, day-over-day metrics |
| 5 | Refusals and failed negotiations emitted as learning signals; skill artifacts as cheaper cognition | The compounding the roadmap is named for |
| 6 | Tool conveyance (licence, sell, grant, teach, inherit), `arrest`, a court verdict | Diffusion and institutions |
| 7 | Addressee column, trace store bound in every mode, `main` caught up with `Real` | A record that can carry the paper |

Two cheap conversation fixes belong beside item 3: a ceiling on
conversation size around five speakers, and nothing else on G1. The judge
says the replies that happen are already human quality.

On the pending "final ten runs on the fixed build" in SERIES.md: do not
spend that budget on ten more six-hour mornings. Finish items 1 and 2
first, then run the series on full days.

---

## 6. The business-readiness question, answered as of today

The project has to end by saying what N engines do in a real business
unsupervised for D days. There is already an interim answer, and it is
worth writing down now rather than in Round 4.

**What it does well.** Negotiates and prices consistently, holds a price
book across a morning, manages a queue by name, remembers and repays
debts, finds the right counterparty with `find_people`.

**Where it fails, and a company would hit each on day one:**

1. **Inattention.** Two thirds of residents addressed by name never speak
   back. Not evasion; they do not initiate.
2. **Thinking instead of acting.** Whole turns of interior monologue with
   no tool call; 1,536-token outputs cut off and resumed.
3. **Losing the thread of place.** Walking to a room that does not exist,
   or standing in the pharmacy while the customer is in "The Willows".
4. **Unpredictable cost per unit of work.** Eightfold spread between
   otherwise identical residents on the same morning.

**The smallest experiment that turns this into a measurement.** One bakery
with an owner, two staff and a few customers, five game-days, against one
scripted rule-based bakery as control. Score solvency, wages paid, tax
paid, stock kept, sales made, and GDP per inference dollar. That is
standing question 27 at rung-1 cost, and it is the shortest path from where
the project is to the thing it has to conclude.

---

## 7. One caution about tools

More tools will not by themselves make the research better. The town has
56 verbs and the loop that gives them consequences is missing. The next
gains come from wiring what exists into stakes and time, then from a small
number of primitives that let capability spread. Count is not the lever.
