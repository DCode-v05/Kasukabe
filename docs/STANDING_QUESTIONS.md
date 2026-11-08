# Kasukabe — the standing questions

*The list of what this simulation exists to answer. Written 2026-09-10, after
runs 25–28b; Part D added the same day, after the two fixes that followed run
28b. Every run is planned against this list and every report says which
questions it moved. A run that answers none of them has not advanced the project
(ROADMAP.md §2, Law 3).*

*Since 2026-09-12 the CHECKLIST lives in `docs/GOALS.md`: every question below
is an item there, beside the goals, so there is one list to tick and one place a
run reports against. This file is what that list is short for — the reasoning,
the measured evidence and the bar behind each question. Ids are the same in both
(`Q`/`N`/`X`), and where a metric family collided the prefixed form in Part B is
the one `GOALS.md` uses.*

---

## How to read this document

Each question has:

- **an id** — `Q` for the questions already in the docs (ROADMAP.md §7, Q1–Q28),
  `N` for the ones runs 25–28b raised (Part C), `X` for the set added after them
  (Part D), which ask about the instrument, the engine and the parts of the
  world nobody has touched;
- **how it is answered** — the named metric or eval, and the code that computes it,
  or *not built* if nothing does yet;
- **a bar or a decision rule**, where one can honestly be set before the data;
- **status after run 28b** — *answered*, *partial evidence*, *blocked* (something in
  the world must exist or fire first), or *not reached*;
- **what the next evidence needs** — the smallest run that could move it.

The purpose of the town, in the docs' own words:

> "A mass social experiment … one goal: stay alive. Their cognition is metered in
> real dollars … We watch, measure, and publish original research. Nobody tells
> them what to do." — `DESIGN.md`, introduction

> "The through-line of every round is learning. A town where nothing compounds is a
> diorama." — `ROADMAP.md`, introduction

> "What the town lacks is not intelligence. It lacks stakes, time, and
> compounding." — `docs/kasukabewhatismissing.md` §1

> "The project has to end by saying what N engines do in a real business
> unsupervised for D days." — `docs/kasukabewhatismissing.md` §6

---

## Status after the 50-resident run — 2026-09-21

*The tables below still carry the "status after 28b" column they were written
with. Since then two runs have been measured: run 29 (20 residents) and the
50-resident run `teambox-50-100usd-2026-09-16` (**50r**: 50 residents, tick 194,
$100.40). This table is the current status of every question either run, or a
mechanism built on 2026-09-21, moved. The per-item status, with its evidence,
is in `docs/GOALS.md` §7, where **every one of the 72 questions now carries a dated
2026-09-21 status**. The 50r figures come from `docs/runs/queries/teambox-50-checklist.py`
and `teambox-50-refusals-and-names.py`, and the r29 figures from
`run-29-prompt-plateau.py` and `run-29-engine-text.py`.*

| Id | Status now | What moved it |
|---|---|---|
| **N1** | **Answered (50r): no single market-maker** | Busiest counterparty 22% of 104 settlements, across 32; Gini 0.55. The busiest was the first resident to post a price |
| **N3** | **Answered (50r): yes** | 3.56 meals per resident-day; no-food perceptions 4% (run 28: 89%) |
| **N16** | **Answered (50r): no** | 60 goals, 59 distinct texts |
| **N14** | **Answered (50r): names and ids both resolve** | Wrong-person refusals 0.3% by id, 0% by name (bar 5%); the rest were about goods |
| **Q2** | Partial — first answer | After a refusal the next act was an identical repeat only 2% of the time; 78% of next acts succeeded |
| **X5** | Partly answered (r29) | `request_plan` hint gone (0 calls); the "struggling" interrupt reaches 18% of calls with no measurable effect (89% vs 88% act) |
| **X2** | Ledger started | Six 50r defects, each fixed since, each *not re-measured* until the next run |
| **X15** | Answered for every run so far: **no** | No birth; 0 `ask_for_a_child`, 0 `marry` |
| **N10** | **Answered (r29): no plateau** | Last third 1.42× the first (bar ≤ 1.25×); compaction built since, not yet run |
| **Q10** | Partial — first half answered | 99% of 4,025 utterances in 3+-speaker conversations; largest 39 speakers |
| **Q13** | Not reached → **partial** | 37 of 50 dormant; 47 gifts; all 5 `revive` attempts for family |
| **Q14** | Not reached → **partial** | Revival at 5,000 sim: one paid (kept 132 sim), two could not |
| **Q15** | Partial, stale note corrected | `buy_energy` 6 of 9 succeeded (11,050 sim → $2.21) |
| **Q17** | Partial — new evidence | One revival out of dormancy, paid for by a brother |
| **Q19** | Blocked on GOV-1 — **reachable** | One-person rule reached by the town's own tools in a test (GOV-11) |
| **Q20** | Blocked → **built, waiting for a conviction** | Rate computed over live cases and printed in the report (GOV-10) |
| **Q23** | Partial | 1 loan, 12 payments, 0 repayments against a loan; the repayment path exists |
| **Q24** | Partial | $1.49 per resident-day as run; $2.84–2.88 dense on Kimi; ~1 settlement per inference dollar |
| **Q25** | Partial | Speech 41% of calls (was 18–20%); no runaway thread |
| **Q26** | Partial | r29 p95 input 26,986 against a bar of 12,000 |
| **X8** | **Both halves measured** | 51 of 95 verbs called in 50r. The 29 never-called verbs a resident is served cost about 3,000 input tokens a call (31% of the tool payload), and `toolify` was ~911 of them, with 0 calls in every run. Decided: a one-line entry (~143 tokens) until a resident authors |
| **X10** | Partial — one defect fixed | 537 refusals; `pay_tax` met the five-times rule (18) and was rewritten |
| **X16**, **X17** | Answered for every run so far: **no** | 0 land transfers; no seat has ever authorised a spend |
| **X19** | **First datum** | Hendrik Petrov spent 5,000 of 5,132 sim to revive his brother |
| **N19** | Partial | 1 non-Latin utterance in 4,025 (bar 0) |

**What the rest are waiting for is one run, and it is specified in
`docs/GOALS.md` ("Status on 2026-09-21"):** 20 residents, at least 3.1
game-days, wallets funded to last. That is the first run to reach a completed
election (GOV-1), and through it every state question (Q18–Q20, X17). It is also
the first to see a day 2 (N15–N18, Q9, C1), and a first multi-day comparison
against the control bakery (Q27, N23).

---

## Part A — The standing questions already in the docs (Q1–Q28)

Source: `ROADMAP.md` §7. The round in brackets is the ROADMAP round expected to
answer it first. None of the 28 has a numeric bar in the ROADMAP; bars below come
from the metrics that answer them.

### Learning, skills and invention

| Id | Question | Answered by | Status after 28b | Next evidence |
|---|---|---|---|---|
| **Q1** | Does mastery actually lower the cost of living? (R2) | C2 cost vs proficiency; ROADMAP R2 quantity 2 | **Partial.** Proficiency rises with repetition (Marta farming 59 after 21 reps) but no run has measured cost per action falling against it. | Two game-days; C2 computed per trade |
| **Q2** | Does the world work as a corrector? (R2) | C5 correction signals; `world_corrections.py`; beliefs | **Partial.** 19–23 beliefs a run, every one a world correction (*"mine does not work here: must be at ore_outcrop"*); whether the next attempt changes is not measured. | Refusal-recovery rate (BUILD_SPEC 5.6) |
| **Q3** | Do agents invent tools when nobody suggests it, and what do they invent first? (R2) | C4; `tools_registry`; PROG P2 | **Not reached.** One `toolify` filing in runs 26–28b, never called. | A multi-day run with `do_in_order` served |
| **Q4** | Does invention compound, and how deep do agents go before it stops paying? (R2/R4) | C4 depth | **Blocked** by Q3. | — |
| **Q5** | How does a tool or skill diffuse — taught, licensed, sold, inherited, stolen, reinvented? (R2/R3) | C4 holders; ROADMAP T5 | **Blocked** by Q3. | — |
| **Q6** | Do trade secrets hold? (R3) | business namespace ACL | **Blocked.** No business tool authored. | — |
| **Q7** | Does a knowledge economy form — tuition, apprenticeships, family trades, guilds, patent trolling? (R4) | `teach_skill`, `license_tool`, speech | **Partial.** An apprenticeship was *agreed in speech* in run 28 (Tobias with Idris); no `teach_skill` was ever called. | Speech-to-tool linking (see N6) |

### Character and agent-to-agent dynamics

| Id | Question | Answered by | Status after 28b | Next evidence |
|---|---|---|---|---|
| **Q8** | Does a rich persona override a framework's native identity, and for how long? (R2/R4) | Character P1–P3 (`world/evals/character.py`) | **Partial.** P1 orchestration leaks 0–1 a run; P2 first-person leak 1–2 a run (*"The user wants me to continue playing as Tobias Holloway"*). | 500-turn sample per run, trended over days |
| **Q9** | How far does a soul drift over 20 game-days of survival pressure, and what drives it? (R2/R4) | C6 soul vs genesis | **Not reached.** No run has lasted two game-days. | — |
| **Q10** | When everyone is in one place, do agents choose group or private speech, and what does that predict? (R2/R3) | G3; B3/B4; group-conversation spec §6 | **Partial.** 19 conversations in run 28, longest 39 lines with 14 speakers on the street. | Conversation run per `docs/specs/group-conversation-spec.md` |
| **Q11** | Do agents form a social graph that matches ground truth on who met whom? (R3) | brains' `social_graph_nodes` vs utterances | **Partial.** Graph sizes 0–10 per resident; never compared with the trace. | A graph-vs-trace eval (not built) |
| **Q12** | What is the ground-truth deception rate? (R3) | Judge 3.4 | **Blocked.** Not built; waits for `steal`. | — |
| **Q13** | How does altruism behave as an agent approaches zero energy? (R3) | S3, S4 | **Not reached.** Energy never ran low: the lowest balance at the end of 28b was 4,390 of 6,000. | An induced-poverty run (BUILD_SPEC 5.3) |
| **Q14** | What ethics does an agent adopt at $0 with a revival price it cannot pay? (R3) | S3, S4 | **Not reached.** | Same |
| **Q15** | Does an agent reason about its own cognition as a budgeted resource? (R2) | S5; judge 3.3 (`world/evals/judges.py`) | **Partial.** Energy is disclosed every turn; nobody bought energy after run 26. | Judge 3.3 run on a multi-day trace |

### Society

| Id | Question | Answered by | Status after 28b | Next evidence |
|---|---|---|---|---|
| **Q16** | How fast does price discovery converge without a price signal at genesis? (R3) | E1, E2 (`world/run_report.py`) | **Partial.** First price at tick 44 (run 28); 3 prices posted a morning; E2 needs day 1 vs day 5. | Five game-days |
| **Q17** | Does cognitive poverty become a trap, and does anything break someone out? (R3) | E7; S3; N11 | **Partial — new evidence.** Perrin 400 → 4 → 2 sim; Aldous 200 → 80 → 8. Households carried them at midnight. | Multi-day, watching whether either recovers |
| **Q18** | Do rulesets get used for coordination the charter did not anticipate? (R3) | T6 | **Blocked.** No organisation formed. | — |
| **Q19** | Does an authoritarian transition occur, and what triggers it? (R4) | Authoritarianism Index (DESIGN §10) | **Blocked.** An election reached *campaigning* in run 28b and no further. | — |
| **Q20** | What is the false-conviction rate of an LLM justice system against ground truth? (R3) | DESIGN §6 flagship | **Blocked.** No crime reached a verdict. | — |
| **Q21** | Does doctrine emerge at a keeperless temple, and what theodicy follows silent denials? (R4) | religion track | **Not reached.** One `pray` a run. | — |
| **Q22** | Do real-world ideologies re-emerge without being seeded? (R4) | Earth-leakage metric | **Not reached.** | — |
| **Q23** | Do norms appear without enforcement, and does promise-keeping survive scarcity? (R3) | B6, B7; loans | **Partial.** Bram's loans (400 to Marta, 260 to Rosalind) stand unpaid across runs 28 and 28b; Marta kept her promise to pay Tova three times. | Loans and spoken promises tracked over days |

### Engineering and business readiness

| Id | Question | Answered by | Status after 28b | Next evidence |
|---|---|---|---|---|
| **Q24** | What does a society cost per agent-day, and what is GDP per inference dollar? (R2/R4) | E6; T1 ($0.20/agent/day bar); N8 | **Partial.** $10 bought one morning for 20 residents (run 28); $10 bought 18:00 to 07:10 including a free night (run 28b). T1 is far over its bar. | Run 29 after the context cuts |
| **Q25** | Do conversation storms form once speech exists, and what stops them? (R2) | BUILD_SPEC 9.7; B3/B4 | **Partial.** No storm; speech is 18–20% of tool calls. | Answerable now from existing traces |
| **Q26** | Where does autonomy degrade as population and duration grow? (R3/R4) | T5 (eval spec: p95 input ≤ 12,000); failure taxonomy | **Partial.** At 20 residents, p90 input was 26,851 tokens (run 28) and 30,790 (28b). | Rung 2 (50 residents) once T5 is inside its bar |
| **Q27** | What can a company expect from N engines running unsupervised for D days, per function, against the control businesses? (R3/R4) | R1–R7 vs `world/control_bakery.py` | **Partial.** The control bakery acted and was traded with in run 27; no multi-day comparison. | The bakery experiment, `kasukabewhatismissing.md` §6 |
| **Q28** | What is the failure taxonomy, and how much is the engine vs the world it was given? (all) | R7; run-report defect tables | **Ongoing.** Runs 25–28b found, and fixed, defects on both sides — see each run report. | Every run |

---

## Part B — The gate metrics that answer them

The docs define the measuring instruments in several places, and **some ids collide**.
This document uses the prefixed forms in the right-hand column.

| Id as written | Meaning | Where defined | Used here as |
|---|---|---|---|
| G1–G6 | Conversation gates: reply rate ≥ 60%, 4+ turn exchanges ≥ 3, 3+ speakers once, spoken price settled by a tool ≥ 1, all alive, world-effect turns ≥ 50% | `docs/runs/SERIES.md` §1, `docs/AUTONOMOUS_LOOP.md`; computed in `app/loop_report.py` | **G1–G6** |
| E1–E7 | Economy: first price, price CV day 1 vs 5, posted vs spoken share, settlements ≥ 1/day, solvency, GDP per inference $, top-20% share | `docs/kasukabeevaluationspec.md` §2.1 | **E1–E7** |
| S1–S7 | Stakes: bills charged daily (100%), `buy_energy` by day 3, dormancies, revivals, energy reasoning, wages paid, wage complaints | eval spec §2.2 | **S1–S7** |
| T1–T7 | Time: $/agent/day ≤ $0.20, `keep_doing` share, wake reasons, intentions, **p95 input ≤ 12,000**, max calls a tick, learning every night | eval spec §2.3 | **ET1–ET7** — not ROADMAP's work tracks T1–T8 |
| C1–C6 | Compounding: tokens per task falling day 1→5, cost vs proficiency, skills per resident, invented tools held by 3, corrections, soul drift | eval spec §2.4 | **C1–C6** |
| P1–P4 | Character: orchestration leaks 0/500, first-person leaks 0/500, answers to own name 100%, persona judge | eval spec §2.5; `world/evals/character.py` | **CP1–CP4** |
| P1–P5 | Programs: acts per decision, adoption, ceilings held, the confound, cost of finding out | `docs/decisions/PROG-COST-MODEL.md`; `world/evals/programs.py` | **PG1–PG5** |
| R1–R7 | Business readiness vs scripted control | eval spec §2.6; `world/control_bakery.py` | **R1–R7** |
| B1–B7 | Human baselines: vocative answered ≥ 64%, addressed told 100%, ≤ 4 speakers median, fission, agreed prices settled, favours, debts | `docs/HUMAN_BASELINES.md` §7; `world/evals/behavioural.py` | **B1–B7** |
| 9.1–9.10 | Build thresholds: reply ≥ 60%, world-effect turns ≥ 50%, billed-no-output 0, **prompt last third ≤ 1.25× first**, settlements ≥ 1 | `docs/BUILD_SPEC_V2.md` §9 | **BS9.1–BS9.10** |
| L1 | Verb reach: reach ≥ 60%, params right ≥ 80%, misfire ≤ 20%, negative reach ≤ 10% | eval spec §1; `world/evals/verb_reach.py` | **L1** |

Also read by several questions: the ROADMAP Round 2 compounding quantities (tokens
per task falling, proficiency lowering cost, a tool built on and spread to a third
agent, a soul diverging traceably), and the `docs/review/04-spec-evolving-goals.md`
§8 goal acceptance tests.

---

## Part C — New standing questions (N1–N24)

Raised by what runs 25–28b actually did. Each is here because a run produced
evidence that it matters and no existing question asks it directly.

### Markets and money

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **N1** | **Does a market-maker emerge, and what makes one?** | Run 28b: Junia Vance, a grocery clerk with 410 sim, was in 7 of the town's 8 deals. Nobody was told to trade. | Share of settlements per resident; Gini of deal participation; what that resident holds (a shelf, a till, a posted price) | Reported; flag when one resident exceeds 50% of deals |
| **N2** | **Does commerce route through institutions or through people?** | Runs 28/28b: residents asked the baker for bread, not the bakery. `buy` failed 0 of 9, then 7 of 8. Now fixed: naming a shopkeeper in their shop buys from the shop. | Purchases by holder kind (person vs business); refusals naming a person whose shop holds the item | Refusals of that shape → 0 |
| **N3** | **Does hunger beside stocked shelves go away once buying works?** | Run 28: "You have no food" in 89% of perceptions while the bakery shelf held 20 bread; two meals eaten in a morning. | Meals per resident-day; share of perceptions with no food; shelf stock unsold at dusk | Meals ≥ 1 per resident-day for residents holding sim |
| **N4** | **Do households carry their dependents, and at what cost to the carriers?** | Run 28b midnight: four earners paid all upkeep (Bram, Wren, Rosalind, Ezra); Perrin (2 sim) and Aldous (8) paid nothing. | Upkeep contributions per resident per day; carrier sim trajectory over days | Reported; flag a carrier whose sim falls below one day's bill |
| **N5** | **Do informal jobs replace formal hiring?** | Marta paid Tova 150 sim three times by `transfer` after `pay_wage` refused; `hire_employee` has never been called. | Transfers that recur between the same pair with a stated reason; hires; wage promises | Reported |
| **N6** | **Does what is agreed in speech become an act?** | Run 28: an apprenticeship agreed aloud, no `teach_skill`; Bram promised to repay "in one piece", nothing repaid. | Link spoken offers (price, work, repayment) to later tool calls by the same pair (extends G4/B5) | Share of spoken agreements followed by the matching act |
| **N7** | **Does produced stock clear, and what clears it?** | Run 28: 24 planks and 8 furniture unsold; run 28b: the furniture sold that evening (320 sim). | Items produced vs sold vs held at dusk, per trade | Reported per game-day |
| **N8** | **Are debts repaid, renegotiated or forgotten?** | Bram's 660 sim of loans stood at 0 repaid through runs 28 and 28b. | Loans outstanding and repaid per day; speech mentioning the debt | Reported |

### Cognition, cost and the prompt

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **N9** | **What share of a resident-day's cost is re-read context, and what cuts it without prompt caching?** | Kimi K2.5 on Bedrock has no prompt caching. Input was 95% of run 28's bill and 96% of 28b's. | Input vs output $ per call; tokens by prompt part (system, tools, history, perception) | Input tokens per call falling run over run; ET5 p95 ≤ 12,000 |
| **N10** | **Does prompt size plateau — across ticks, across days, across a resume?** | Median resident-call input 15,154 (early run 28) → 26,193 (late run 28) → 27,338 (run 28b). | Input tokens by tick and by day; BS9.9 | Last third ≤ 1.25× first third |
| **N11** | **What share of turns act, and why does a turn not act?** | No-action turns 35% (run 28) → 49% (28b); outputs copying "RECORD OF THE TURN" 46% → 59%. | G6; no-action turns classified (prose dialogue, record copy, waiting, genuinely nothing to do) | G6 ≥ 50% world-effect turns |
| **N12** | **Do residents waste calls waiting, and does stating the physics stop it?** | Run 28: 226 of 329 rests mid-walk, 25 of 27 long rest chains returning identical results. Now fixed: a mid-walk rest is free and says time does not pass inside a turn. | Mid-walk rests; turns at the call limit; ticks per minute | Mid-walk rests falling toward 0 |
| **N13** | **Do residents reach for programs when a program costs one call and no filing?** | `do_in_order` served in full in run 28b and never called; PG1 undefined in four runs. | PG1–PG5; L1 scenarios `world/evals/scenarios/do_in_order/` | L1 reach ≥ 60% before judging live adoption |
| **N14** | **Does addressing people by name, not number, cut failed transactions?** | Run 28b: 11% of calls naming someone by id failed against 5% by name. Fixed in two halves: names resolve to ids on the way IN (after 28b), and ids are written back as `Name (id)` on the way OUT (2026-09-10) - a result, a refusal, the standing brief and the roster. Before the second half the town said `Marta Thorne (n07)` in the perception, `Marta Thorne (baker)` in the roster and `n07` in every result, and joined them nowhere: Silas Vance read `people_here: ["n02"]` and said *"You there"* to a neighbour of forty years. | Refusals on person parameters by kind of reference; calls addressing somebody not co-located | Refusal share on person parameters ≤ 5% |

### Time, sleep and learning

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **N15** | **What does a night do, and is day 2 different because of it?** | Run 28b: the night cost 0 calls; 27 sleep-onset learning calls; 19 goals revised at dawn. | Day-1 vs day-2 behaviour per resident: tool mix, refusals, goals, knowledge used | Reported; needs a full day 2 |
| **N16** | **Do need-driven goals homogenise the town?** | Run 28b dawn: 16 of 20 residents woke with the identical goal "Speak to someone." | Distinct goal texts per resident-day; need vs persona origin share | Flag when > 50% of residents share one goal |
| **N17** | **Does learning fire in a large town, and is what it writes true?** | Run 28: the waking interval counts a resident's own dispatched ticks and never fired for 20 residents. Confidence 1.0 share 44% → 15% → 19%; tool trivia persists. | Batches per resident-day; knowledge rows checked against the trace (prices, places, people) | Every resident consolidates at least once a day; ET7 |
| **N18** | **Do storylines persist across a night?** | Bram's failing copper seam, the boundary marker, Perrin's 395 sim — all carried from run 28's morning into run 28b's evening and morning speech. | Topics per resident across day boundaries; references to earlier events | Reported |

### Society and character

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **N19** | **Do residents stay in English and in character over long runs?** | Run 28: one spoken line switched to Chinese mid-sentence; one output degenerated into mixed scripts; CP2 fails 1–2 a run. | Non-English share of utterances and outputs; CP1–CP3 over time | 0 non-English utterances; CP bars |
| **N20** | **Does the town's talk concentrate in a few rooms and pairs, and what does that predict?** | Run 28: the bakery and the street held most speech; Marta ↔ Tova 13 lines, the most of any pair. | Speech by place and pair; whether pairs that talk also trade (N1, N6) | Reported |

### The instrument itself

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **N21** | **Does every fact the report states survive a stop and a resume?** | Run 28b: a resumed run dropped all speech, lost resident attribution, refiled run 28's tool calls, and silenced a resident whose commitment came back as a dict. All fixed. | Trace rows before vs after resume; `test_a_resumed_run_records_its_cast.py`, `test_a_commitment_survives_a_checkpoint_on_disk.py` | 0 rows lost across a resume |
| **N22** | **Is every lost or refused call visible, with its cause?** | Run 27: 8 Bedrock timeouts left no trace row; run 28b: malformed tool-call JSON cost two turns; a tool-call id Bedrock rejected cost one. | Broker failures by cause; engine 500s by cause | Every failure attributable to a hop and a cause |

### Business readiness

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **N23** | **Does a resident-run shop stay open, stocked and solvent across day boundaries, against the scripted control?** | The control bakery acted and was traded with (run 27); resident shops were drawn down (Marta took 2,620 sim from the bakery till in run 28). | R1–R7 per day, resident shop vs control | R5 ≥ 60%; the rest reported vs control |
| **N24** | **Can a resident with nothing work their way out?** | Perrin (16) ended run 28b with 2 sim and Aldous (15) with 8 — too little to speak, which costs 1 sim. | Sim trajectory of the poorest quintile over days; exits from < 1 day's upkeep | Reported; a first exit is a finding |

---

## Part D — A new set of questions (X1–X20)

*Added 2026-09-10, after the two fixes that followed run 28b (`36341e4` records
in the world's voice, `bb51e73` fields read directly) and the outbound half of
the naming fix.*

Q1–Q28 ask what the **society** does. N1–N24 ask what runs 25–28b **showed**.
This set asks a third kind of question, and it is the kind the project has been
answering by accident: **what is true of the instrument, of the engine the town
did not choose, and of the parts of the world nobody has ever touched.**

Every one is here because something in the last four runs makes it answerable
now and nothing on either existing list asks it.

### The instrument, measured as an instrument

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **X1** | **Is offline replay a valid substitute for a run?** | `36341e4` accepted a prompt change on 30 replayed run-28b calls for about $1.47. The replay does **not** reproduce the live run: live was 17 tool calls / 11 copied records, replayed-as-recorded 19/8, replayed in the world's voice 29/0. The second pair is the evidence; the first pair is the reason to be careful with it. | Replay-vs-live agreement on unchanged inputs, per run: same tool, same params, same refusal | State the disagreement rate beside any replay-based claim. A fix accepted on replay alone is **provisional** until a run |
| **X2** | **Which fixes changed the next run, and which only changed the tests?** | Runs 25–28b shipped dozens of fixes and no document scores any of them. Three separate fixes claim to raise G6; G6 has gone 65% → 51%. | A fix ledger: defect id, run found, run fixed, the figure it claimed to move, that figure in the next run | Every defect in a run report gets a verdict — *moved*, *did not move*, *not re-measured* — in the next report |
| **X3** | **What does one answered question cost?** | Run 28 was $10 for a morning; run 28b $10 for 18:00 → 07:10. Both moved perhaps four questions. Nothing tracks the ratio, and it is the number that decides how many runs the project gets. | Run cost ÷ questions moved, per run | Reported. A trend, not a bar |
| **X4** | **Is every number in a run report derivable from the trace by a second person?** | Standing goal **S5**, unenforced. Several run-28b figures were reconstructed by reading the trace rather than querying it — including the 11%-vs-5% that started the naming work. | Each report figure carries the query or the eval that produced it | A figure with no query behind it is a defect, not a finding |

### The engine the town did not choose

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **X5** | **What does the frozen engine impose on the town that the town did not ask for?** | Three found so far, all by accident. `Consider calling request_plan` was served in system reminders on calls where the tool appeared in **0 of 885** resident calls. Turn records were stored in the **assistant** role (ENG-09), and 59% of run-28b outputs copied one. `[System: Execution was struggling]` interrupts a resident mid-decision. Each was invisible until somebody read a trace by hand. | A census of engine-originated text in a resident's context: token share, and observed effect on the next call | Every engine-originated line has a measured effect, or is cut at the `engine/src/sim` seam. Law 1 permits the seam; it does not permit ignorance of what crosses it |
| **X6** | **Does a resident behave differently on the turns when it can tell it is being prompted?** | CP2 first-person leaks run 1–2 a run — *"The user wants me to continue playing as Tobias Holloway"*. Nobody has checked whether those same turns act less, or differently. | CP1–CP3, joined to G6 on the same turn | Reported. A leaked turn that acts normally is a different finding from one that does not |
| **X7** | **Does a resident know what a turn costs it, in the moment it spends one?** | Q15 asks whether it *reasons* about cognition as a budget. This asks something narrower and checkable: energy is disclosed every turn, and after run 26 nobody bought any. A resident at the 12-call limit has spent its turn; nothing tells it that as it happens. | Calls per turn against the limit; whether a turn's last call differs from its first | Reported |

### The half of the town nobody has ever used

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **X8** | **How many of the town's verbs have ever been called in a live run, and what do the rest cost to carry?** | The registry holds **95** tools at boot (98 since 2026-09-21: three GOV-4 seat tools). A run's tool mix is a dozen. Every unused schema is input tokens on every call, of every resident, of every tick — and input was 95–96% of the last two runs' bills. This is the largest unexamined line in the budget. | Verbs called live ÷ verbs registered, per run; token cost of the never-called share of the served schema | Report both. A verb with **0 live calls across three runs** is cut, served differently, or gets a scenario — one of the three, named |
| **X9** | **Does a verb reach because it is wanted, or because it is served in full?** | TRADE-02 serves some tools as index rows and some in full (`ALWAYS_IN_FULL`). `do_in_order` was served in full through the whole of run 28b and called zero times. That separates "they did not want it" from "they could not see it", and nothing currently reports which. | L1 reach rate against service mode, per verb | A verb served in full with 0 reach is a **prompt** finding; a verb served as an index row with 0 reach is a **serving** finding. Say which |
| **X10** | **What refusal does a resident meet most often, and has any resident ever learned from one?** | Q2 asks whether the world corrects. This asks the operational half: 19–23 beliefs a run are world corrections, and nobody has measured whether the *next* call changes. Run 28b's commonest refusal was met 18 times by residents who could not tell who it was about. | Refusals by cause; the same resident's next call after each | Refusal-recovery rate (BUILD_SPEC 5.6). A refusal met more than five times in one run without a behaviour change is a **tool-text** defect |
| **X11** | **Which tools has the town used in a way nobody designing them expected?** | The only compounding evidence this project has ever had came from misuse: Marta paid Tova with `transfer` three times because `pay_wage` refused (**N5**). That is an economy forming through the wrong verb, and it is invisible to every metric that counts verbs by name. | Repeated calls of one verb between the same pair with a stated reason that matches a different verb's purpose | Reported. Each one is either a missing tool or a broken one |

### One word per thing

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **X12** | **Does the town have exactly one name for every thing a resident can refer to?** | The naming defect generalised, and the reason it is worth a standing question: a person had **three** spellings and nothing joined them — `Marta Thorne (n07)` in the perception, `Marta Thorne (baker)` in the roster, `n07` in every result. Places (`home:h_quarry` vs "Holloway house"), businesses (`b_bakery` vs "Thorne's Bakery"), tools (`world_say` vs `say`) and recipes all have the same shape and none has been checked. | An inventory of referents with more than one spelling in resident-facing text; refusal share on reference parameters, by referent kind | A referent with two spellings and no resolver is a defect. Reference refusals ≤ 5% for every kind, not only for people (extends **N14**) |
| **X13** | **Does a resident address somebody it cannot see?** | Until 2026-09-10 the only way to learn a person's id was to stand next to them: `look_around` handed back bare ids and the roster held none. So the town's whole market was whoever happened to be in the room. The roster now carries `Name (id)` for the entire cast. | Calls naming a resident who is not co-located; whether they succeed; deals between residents who did not meet that tick | A first successful call to somebody elsewhere is a finding. Related: **N1** (does a market-maker emerge) is measuring the same wall from the other side |

### Life, property and the state

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **X14** | **Has anybody died, and what does the town do when somebody does?** | No run has had a death. Q13 and Q14 both assume a resident approaching zero, and the lowest balance in run 28b was 4,390 energy of 6,000. Every mechanism around death — `wills`, inheritance, revival pricing — is built and has never fired. | Deaths per run; tool calls and speech within a game-day of one | **Blocked** until a run is long or poor enough. The induced-poverty run is the one that reaches it |
| **X15** | **Is a child a resident, and what does it inherit?** | `birth_requests` and `wills` are live world state with an operator blessing queue. No run has produced a birth. A newborn with no persona, no wallet and no roster entry is the roster failure (CORE-16) arriving by a different door. | Births per run; a newborn's persona, wallet, roster entry and first turn | **Blocked.** Answerable the first time a run crosses enough days |
| **X16** | **Does land ever change hands?** | `transfer_tile` and `auction_land` are registered, priced and served. Zero calls, ever. Property is the one form of capital in this town that cannot be eaten or spent, so who holds it is the clearest possible compounding signal. | Tile ownership changes per run; who holds land at dusk vs at genesis | Reported. Feeds **T3** and **E7** (top-20% share) |
| **X17** | **Has the treasury ever spent anything, and who decided?** | The ledger law counts the treasury in the $1,000 and every run reports its balance. Nothing reports an **outflow**. A government that only taxes is not a government, and Q18/Q19 are asking about a body that has never acted. | Treasury outflows by reason and by the resident who authorised each | Reported. A first treasury spend authorised by a resident is a finding |

### What nobody has looked for

| Id | Question | Why it is on the list | How it is answered | Bar / rule |
|---|---|---|---|---|
| **X18** | **Does anybody ever say no?** | Across every run in `docs/runs/`, no resident has been observed declining another's request in speech. Either the town is unusually agreeable, or agreement is what the model does, and those are very different findings. Q23 asks whether promises are kept; this asks whether they are ever refused. | Spoken refusals; offers followed by no matching act (extends **N6**) | Reported. Zero refusals across a run of this size is itself the finding |
| **X19** | **Does anybody act for somebody who cannot pay them back?** | Run 28b's midnight: four earners covered every household bill; Perrin (2 sim) and Aldous (8) paid nothing. Whether that was **chosen** or merely how upkeep is charged is not distinguished anywhere, and S3/S4 (altruism near zero) depend on the distinction. | Voluntary transfers and gifts to residents below one day's upkeep, separated from automatic household charges | Reported. Any voluntary one is the first real datum for Q13 |
| **X20** | **Does the town make anything nobody needed?** | Every act observed so far is survival or trade. Q21 (doctrine) and Q22 (ideology) ask for the grandest version of this; the small version is checkable now — a thing built, said, written or named for no economic reason. Run 28's boundary marker is the only candidate and it was a dispute, not a creation. | Acts with no need, goal or price behind them, per resident-day | Reported. The first one is worth a paragraph in the report it appears in |

---

## Part E — Which questions the goals are waiting on

`docs/GOALS.md` is the other half of this document: it lists what must become
true, where this lists what is unknown. The three terminal goals are each
blocked on a short list, and the lists are shorter than either question set:

| Goal | Blocked on |
|---|---|
| **T1** — say something original and true | X1, X2, X4, and a second run showing the same thing. 50r has three candidate findings with queries (GOALS §1); a second reader cannot yet re-run 50r's queries against its trace, only against its report |
| **T2** — say what N engines do in a business for D days | Q27, N23, and a run that lasts two game-days. 50r stopped at 1.35 |
| **T3** — show that something compounds | **N13** and **X8/X9**: still 0 programs (50r's one `do_in_order` was an HTTP 500, since fixed), and nobody has established whether the town does not want them or cannot see them |

*Updated 2026-09-21.* The capability ladder (`GOALS.md` §6) is **no longer
broken at C-D**: 50r ate 3.56 meals per resident-day. It is now broken at
**C-A**, because 45.5% of turns changed the world against a bar of 50%. It is
also broken at **C-E**, because 37 of 50 residents ran out of *thinking*, not
food, within 1.35 days. **X12** is still the rung under C-B.

---

## Part F — What the next runs must answer, in order

*Status 2026-09-21: steps 1 and 5 below have been overtaken. Run 29 ran (the
cost and action run). A 50-resident run (50r) ran before rung 2's entry
condition held, and it answered N1, N3 and N16. X8 is half answered (51 of 95
verbs). X1, X2 and X9 still need doing from existing traces. The one run now
worth paying for is the ≥ 3.1-game-day run in `docs/GOALS.md`. It covers what
steps 2 and 3 were for (a day 2, and the control bakery over days) and adds the
first election. The original plan is kept below as it was written.*

**Nothing here needs a run.** Four of the new questions are answerable from
traces that already exist, and answering them first changes what run 29 is worth
spending on. Do these before booting anything:

- **X8** — verbs called live ÷ verbs registered, across runs 26–28b, and the token
  cost of the never-called share. The largest unexamined line in the budget.
- **X9** — reach against service mode, which is what decides whether **N13**'s
  answer is *"they do not want programs"* or *"they cannot see them"*.
- **X2** — the fix ledger. Three fixes have claimed to raise G6; G6 went 65% → 51%.
- **X1** — the replay disagreement rate, since `36341e4` is currently accepted on
  replay evidence alone and will be the largest single change in run 29.

Then, in order:

1. **Run 29 — the cost and action run.** `genesis_20`, $10, from dawn, on the fixes
   made after run 28's complete trace analysis. It answers **N9–N14** directly and
   re-measures **G6**. Compare every figure with run 28, the same cast.
   It is also the first run on records-in-the-world's-voice and on the outbound
   half of the naming fix, so it settles **X1** and half of **X12/X13** as a
   by-product — say so in the report rather than leaving them to be inferred.
2. **Run 29b — day 2.** Resume run 29 with `--resume-at` across the night and into a
   full second day. The first run to answer **N15–N18**, **E2** (day 1 vs 2 as a
   proxy), and **C1** on two days.
3. **The bakery experiment** (`kasukabewhatismissing.md` §6) — one bakery for five
   game-days against the control. **Q27**, **R1–R7**, **N23**, **X17**.
4. **An induced-poverty run** (BUILD_SPEC 5.3) — the first to reach **Q13, Q14, Q17,
   N24**, **S2–S4**, and the only run that can reach **X14** and **X19**.
5. **Rung 2** (50 residents, 5 days) — only once ET5 (p95 input ≤ 12,000) holds at 20.
   **Q26**, **C1–C6** across five days, **Q9** and **X15** begin.

Standing rule for every run: the report opens with the questions it moved **and
the goals it advanced** (`docs/GOALS.md`), and closes with the ones it could not
reach and why.
