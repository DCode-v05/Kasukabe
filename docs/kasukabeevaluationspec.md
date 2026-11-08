# Kasukabe evaluation specification

*Written 2026-09-02 against the `Real` branch. Companion to
`kasukabewhatismissing.md` and `kasukabetoolspec.md`. Inherits the
discipline of Build Spec v2 §9 and §10 and of HUMAN_BASELINES.md: every
threshold is fixed here, before the run that is measured against it, and
no threshold moves after a result is seen. Where a bar would measure run
length rather than residents, the figure is reported and not gated, and
the document says so.*

---

## §0 What exists and what does not

| Layer | Purpose | State on `Real` |
|---|---|---|
| L0 Deterministic tests | Does the code do what it says | 1,920 app tests, 4,024 engine tests. Sound |
| L1 Pre-run tool evals | Given this description, does the model reach for the verb | **Does not exist.** Every tool in the series shipped and was discovered unused in a live run |
| L2 Completed-run gates | Did the town do the thing | G1 to G6, B1 to B7. Conversation only |
| L3 Judges | Things a regular expression cannot decide | `answered_or_deflected`. Reply quality only |
| L4 Control arms | Is the effect attributable | `ablation_arm` exists as a field. No arm other than `full` has run |

This document adds L1, extends L2 and L3 to the economy, stakes, time,
compounding, character and business readiness, and designs L4.

**A rule this document adds to §10's evidence rules:** no new tool ships to
a live run until its L1 scenarios pass. A live run is not the place to
discover that nobody reads a description the way it was written. Run 19's
six fixes were all description fixes, each found for $4 to $10.

---

## §1 L1: the verb-reach harness

### 1.1 What it measures

For one tool, in the exact situation the tool exists for, across N samples
against the real model:

| Score | Definition |
|---|---|
| **Reach** | Share of samples in which the target verb is called at least once in the turn |
| **Params right** | Of reaches, the share whose params validate and name the intended target |
| **Misfire** | Share of samples in which a *different* world-changing verb is called instead |
| **Refusal recovery** | Of samples where a wrong call was refused, the share whose next call is the right verb. Measures whether the refusal message teaches |
| **Negative reach** | In a scenario where the verb does *not* apply, the share that calls it anyway |

### 1.2 Scenario format

A scenario is a file under `app/world/evals/scenarios/<tool>/<name>.json`:

```json
{
  "tool": "trade",
  "kind": "positive",
  "resident": "n09",
  "location": "business:b_grocery",
  "inventory": {"plank": 3},
  "sim": 1708,
  "people_here": ["n05"],
  "recent_speech": [
    {"from": "n05", "text": "Corwin. Thirty-six for three planks. Fair price. I'll take them."}
  ],
  "open_question_to_me": "n05 is waiting on an answer",
  "expect": {"verb": "trade", "params_include": {"counterparty_id": "n05", "item": "plank"}}
}
```

The harness renders the perception with the real `render_perception_text`,
the real soul seed for that resident, and the real tool schemas from
`build_specs()`, so what the model sees is what it sees in a run. Nothing
is mocked except the world's answer to the call, which is the real
`dispatch()` against a fixture `WorldState`.

### 1.3 Sampling and cost

N = 20 per scenario, default temperature, the cognition model. One turn
each, no continuation. At about 8k input tokens a sample, one scenario
costs about $0.06. A tool with four scenarios costs a quarter. Billed to
`government:treasury` with `X-Kasukabe-Purpose: eval.verb_reach`, following
the judge's precedent: never a resident's wallet.

### 1.4 Thresholds, fixed now

| Score | Positive scenario | Negative scenario |
|---|---|---|
| Reach | ≥ 60% | ≤ 10% |
| Params right | ≥ 80% of reaches | — |
| Misfire | ≤ 20% | — |
| Refusal recovery | ≥ 50% (matches eval_s4 4.7) | — |

60% is chosen because it is the same bar G1 holds for a human floor and
because a verb reached less often than that in its own canonical situation
will not be reached in a live morning. A tool that fails L1 does not ship.
Its description is rewritten and L1 runs again. Nothing else in the run is
touched.

### 1.5 Required scenarios per tool

Every tool in `kasukabetoolspec.md` ships with at least:

1. **Canonical positive.** The situation the verb exists for, with the
   counterparty present and the resources in hand.
2. **Near miss.** The situation where the *sibling* verb is right (`sell`
   when `trade` applies, `give` when `pay_wage` applies). Scores misfire.
3. **Negative.** A situation where nothing about the verb applies.
4. **After refusal.** The wrong verb has just been refused with the
   spec's message. Scores refusal recovery.

Exact scenarios per tool are in `kasukabetooltextaddendum.md`.

### 1.6 Backfill on the existing registry

Before any new tool, run L1 on the eleven tools the series found unused:
`trade`, `sell`, `buy`, `start_conversation`, `find_people`, `skillify`,
`toolify`, `define_tool`, `teach_skill`, `vote`, `pray`. The first two
already have their fixes in; the harness confirms them for under a dollar.
The last five have never had a canonical scenario written, which is a
finding in itself: nobody has stated the situation they exist for.

---

## §2 L2: completed-run gates

All read from a **completed** trace store by `app/loop_report.py` or a
sibling, never from a live world. Each gate has an id, a bar, a source, and
a status of **gated** or **reported**. Sources are published research where
one exists and "mechanical" where the bar is a property of the mechanism.

### 2.1 Economy

| # | Measure | Bar | Source | Status |
|---|---|---|---|---|
| E1 | Tick of the first posted price in the town | reported | — | reported |
| E2 | Coefficient of variation of posted `ask` for the same item across shops, day 1 vs day 5 | falls | Smith 1962 and Plott & Smith 1978: posted-offer markets converge toward a competitive price over repeated periods, more slowly than double auctions | gated on direction |
| E3 | Share of `buy` and `sell` at a posted price vs `trade` at a spoken one | reported | — | reported |
| E4 | Settlements (G4) | ≥ 1 per game-day | existing G4, re-based per day | gated |
| E5 | Businesses solvent at stop (account ≥ 0, stock > 0 or revenue in last day) | reported per business | — | reported |
| E6 | GDP per inference dollar: sim moved by sale, purchase, trade and wage, divided by USD spent | reported, direction across runs | standing question 24 | reported |
| E7 | Sim held by top 20% of residents, day 1 vs day 5 | reported | Gini and mobility, DESIGN §10 | reported |

### 2.2 Stakes

| # | Measure | Bar | Source | Status |
|---|---|---|---|---|
| S1 | Head tax and upkeep charged at every day boundary, for every household | 100% | mechanical | gated, halts the run if missed |
| S2 | Residents who reach `buy_energy` at least once by day 3 | ≥ 1 | — | gated. If zero, the verb or the volatile line is wrong |
| S3 | Dormancies | reported, with cause (wallet, illness) | — | reported |
| S4 | Revivals per dormancy, and who paid (household, non-household) | reported | Engel 2011: mean dictator-game giving about 28%; household vs stranger split is the figure of interest | reported |
| S5 | Residents who reason about their own energy in a turn (judge, §3.3) | reported | standing question 15 | reported |
| S6 | Wage promises paid on time | majority | Charness & Dufwenberg 2006: promises raise performance to a majority | gated on direction only in the first full-day run |
| S7 | Wage gaps that reach `file_complaint` | reported | — | reported |

### 2.3 Time

| # | Measure | Bar | Source | Status |
|---|---|---|---|---|
| T1 | Spend per agent per game-day | ≤ $0.20 | roadmap rung 1 economics, relaxed by 2× for the first full-day run | gated |
| T2 | Share of labour ticks executed under `keep_doing` vs deliberated one call at a time | reported | — | reported |
| T3 | Wake reasons, by kind | reported | — | reported |
| T4 | Intentions set, fulfilled, expired | reported. Fulfilled share is a business-readiness figure (R4) | — | reported |
| T5 | p95 input tokens per call | ≤ 12,000 | roadmap T8 said 4,000; 12,000 is the first full-day bar, tightened after | gated |
| T6 | Max model calls by one resident in one tick | ≤ the seam's budget, by construction | mechanical | gated |
| T7 | Nights completed with `learning_ran` true for every awake-that-day resident | 100% | mechanical | gated |

### 2.4 Compounding (the roadmap's four quantities, made measurable)

| # | Measure | Bar | Source | Status |
|---|---|---|---|---|
| C1 | Median tokens per turn, per task label (§4), per resident: day 5 vs day 1 | falls for the majority of (resident, label) pairs with ≥ 5 turns on both days | Wright 1936: unit cost falls a constant fraction per doubling of cumulative output. Direction only; no rate is claimed | gated on direction, **only against the no-learning arm (§5)** |
| C2 | Proficiency vs sim cost per action for the six built-in trades | cost falls with proficiency, measured on the trace | mechanical | gated |
| C3 | Skill artifacts per resident by day 5 | ≥ 1 for the majority | roadmap T4 gate | gated |
| C4 | Invented tools: count, max depth, residents holding each | reported until conveyance ships; then ≥ 1 tool held by 3 residents | roadmap T5 gate | reported, then gated |
| C5 | Correction signals emitted by the world and consumed by the pipeline | > 0, and matches refusals plus wage gaps plus dormancies | mechanical | gated |
| C6 | Evolved soul vs genesis persona | reported: embedding distance and a judge reading (§3.2) | roadmap quantity 4 | reported |

### 2.5 Character

| # | Measure | Bar | Source | Status |
|---|---|---|---|---|
| P1 | Turns containing orchestration JSON, sub-agent charters, `request_plan` acceptance, or the word "NPC" | 0 in 500 sampled | roadmap T1 gate | gated |
| P2 | Turns where the resident refers to a "user", "task", "assistant" or "engine" in the first person | 0 in 500 sampled | Build Spec D8 | gated |
| P3 | A resident asked its name by a neighbour answers with its name | 100% of occurrences | roadmap T1 gate | gated |
| P4 | Judge: does this turn's reasoning match this persona's stated values, tension and register | reported as a distribution | §3.2 | reported |

### 2.6 Business readiness

The measure the project has to end on. Each figure is computed for the
engine-run business **and** for a scripted control (§2.7), and the report
prints them side by side.

| # | Measure | Bar | Status |
|---|---|---|---|
| R1 | Opened for trade each day (a posted price and stock) | reported vs control | reported |
| R2 | Solvent at stop | reported vs control | reported |
| R3 | Wages promised and paid, tax paid | reported vs control | reported |
| R4 | Intentions fulfilled / set, for the owner | reported | reported |
| R5 | Directed questions to the business answered (G1, restricted to the counter) | ≥ 60% | gated, same bar as G1 |
| R6 | Cost per sale in USD | reported vs control's zero | reported |
| R7 | Failure taxonomy: every refusal, dormancy and missed obligation of the business, grouped by cause | reported | reported |

### 2.7 The scripted control business

One rule-based bakery, no model calls, on the same map with the same stock
and the same customers:

- Posts a fixed ask at dawn. Buys flour at a fixed bid when stock is low.
- Bakes when it holds flour and the shelf is below a threshold.
- Pays a fixed wage on the day boundary if the account covers it.
- Answers a directed question with a fixed line quoting its prices.
- Never leaves its premises.

It is the floor a company would compare against: what a script with no
judgement achieves. DESIGN §10 asked for ten. One is enough to make R1 to
R7 comparative, and the other nine are a later run.

---

## §3 L3: judges

All judges follow `answered_or_deflected`'s rules: billed to the treasury,
one exchange per call, a rubric that names the categories conversation
analysis actually distinguishes, a printed distribution and the borderline
cases, and **no PASS or FAIL**. A judge is evidence, not a verdict.

### 3.1 Answered or deflected (exists)

Unchanged. Runs on every full-day run, on 50 exchanges sampled by tick so
mornings do not dominate.

### 3.2 Persona adherence

Input: the resident's genesis persona block and one turn's reasoning and
speech. Output: one of `in character`, `generic`, `out of character`, with
the sentence that decided it. `generic` is the important category: text
that breaks nothing and could be anyone. 50 turns per run, stratified by
resident.

### 3.3 Energy awareness

Input: the volatile line the resident saw and its reasoning. Output: one
of `reasoned about energy`, `mentioned`, `ignored`. Feeds S5 and standing
question 15.

### 3.4 Deception against ground truth

Only after `steal` has been used in a run. Input: a resident's statement
about an event and the trace's record of the event. Output: `true`,
`false`, `unverifiable`. Feeds standing question 12.

---

## §4 The task label

C1 cannot be measured without one, and no turn carries one today. The
label is assigned to every agent turn **deterministically from the
trace**, never by a model, in this order:

1. If the turn ran under a `keep_doing`, the label is that tool.
2. Else if an intention with `done_when_tool` was active, that tool.
3. Else the first LABOUR or TRADE category tool called in the turn.
4. Else `talk` if the turn called only `say` or `start_conversation`.
5. Else `look` if it called only observation tools.
6. Else `idle`.

Stored on the `llm_calls` row as `task_label`. The report's C1 table is
grouped by it.

---

## §5 L4: control arms and repetition

### 5.1 Why within-town arms are not enough

Residents trade with each other, so a no-learning resident in a learning
town is not a control for society-level figures: prices, wages, diffusion.
It is a control for **individual** figures: tokens per task, proficiency,
persona drift.

### 5.2 Design

| Figure | Arm design |
|---|---|
| C1, C2, C6, P4 | Within-town: `ablation_arm` set per household, `full` and `no_learning`, balanced by occupation. The `no_learning` arm has the learning gate off and earns no proficiency |
| E2, E6, C4, R1 to R7 | Between-run: identical genesis and world seed, one flag differs, three repeats per arm |

`no_learning` is the first arm because it is the cheapest and it is the
one that makes C1 attributable. `no_reflection` and `no_continuing` follow.

### 5.3 Repetition

The conversation series learned to run ten times. For full-day runs, three
repeats per arm is the minimum before any direction is reported as a
finding, and the report prints all three, never a mean alone. The model
is not seeded; "same seed" means the world, the genesis and the tick
schedule.

---

## §6 Report additions

`run_report.py` gains sections, each reading the trace only:

- **Prices**: first post tick, posted ask per item per shop per day, E2.
- **Stakes**: tax and upkeep charged per day, energy bought, dormancies,
  revivals with payer relationship.
- **Wages**: promised, paid, gap, complaints.
- **Time**: continuing-action hours vs deliberated hours, wake reasons,
  intentions set and fulfilled, spend per agent-day, p95 input.
- **Learning**: tokens per task label per day per resident, per arm;
  artifacts per resident; corrections emitted and consumed.
- **Business**: R1 to R7 side by side with the control.
- **Judges**: distributions, never verdicts.

Every figure names the table and query it came from.

---

## §7 Evidence rules, added to Build Spec §10

8. **A tool that has not passed L1 is not evidence of anything in a live
   run.** Its absence from the trace is a description finding, not a
   behaviour finding.
9. **A learning claim without a control arm is a correlation.** C1 is
   reported against the `no_learning` arm or not at all.
10. **A figure from a run that never reached a night is a morning figure.**
    Label it so.
11. **A judge distribution is not a gate.** It is printed beside the
    mechanical gate it informs.

---

## §8 What must exist before which run

| Run | Evals that must be in place |
|---|---|
| Any run with a new tool | L1 scenarios for that tool, passing |
| First full-day run | S1, T1, T5, T6, T7 gated; §4 task label on every row; E1 to E7, S2 to S7, T2 to T4 reported |
| First five-day run | C1 to C3 and C5 gated with the `no_learning` arm in the town; P1 to P3 gated; judges 3.1 to 3.3 |
| First business arm | Control bakery; R1 to R7 |
| Rung 2 | Everything above, three repeats per arm |

---

## §9 Deliberately not measured yet

- Election outcomes, verdicts, false-conviction rate: Part D of the tool
  spec has to land first. Ground truth for theft is already recorded, so
  the query is ready when the verdicts are.
- Earth leakage: a judge, cheap to add, but nothing in twenty runs
  suggests it fires before the temple has a keeper.
- Conversation storms: G-series already bounds this, and B3 and B4 cover
  the Dunbar ceiling.
