# Kasukabe — the checklist

*One list. Everything this project must be able to **say**, everything it must
**find out**, and everything that must be **true of every run** — as items that
can be ticked, each with the metric, eval or trace that decides it.*

*Rewritten 2026-09-12; every status re-checked 2026-09-21 (see "Status on
2026-09-21" below). It replaces the split between this file and
`docs/STANDING_QUESTIONS.md`: the questions are now items here, and that file
remains as the long-form reasoning behind each one. A question asks what is
true; a goal says what must become true; both are unticked boxes until
something in the trace says otherwise.*

---

## How to read this

- **Every line is a checkbox, and only evidence ticks it.** The decider is
  always named: a metric id, an eval, a guard, or a query over the trace. "It
  looked like it worked" ticks nothing.
- **An id never changes meaning.** Three collisions were live in the old pair of
  documents and are settled here: `T1–T3` are the three terminal answers and
  nothing else (the eval spec's time metrics are `ET1–ET7`, the ROADMAP's work
  tracks are cited as `ROADMAP T*`); the standing guarantees are `SG1–SG8` (the
  eval spec's stakes metrics keep `ES1–ES7`); the capability ladder is
  `C-A…C-I`, and `C1–C6` always means the compounding metrics.
- **Question ids are unchanged** — `Q1–Q28` from ROADMAP §7, `N1–N24` raised by
  runs 25–28b, `X1–X20` added after them.
- **Status words:** *held*, *partial*, *not reached*, *blocked*. A box is ticked
  only at *held*.

> "A mass social experiment … one goal: stay alive. Their cognition is metered
> in real dollars … We watch, measure, and publish original research. Nobody
> tells them what to do." — `DESIGN.md`, introduction

---

## Status on 2026-09-21

*Every box below was re-checked on this date against three kinds of evidence,
named where they are used:*

- **50r** — `teambox-50-100usd-2026-09-16`: 50 residents, tick 194 (1.35
  game-days), $100.40. Its trace database stayed on the machine that ran it, so
  every figure is computed from its report, which carries every turn and every
  utterance verbatim: `docs/runs/queries/teambox-50-checklist.py`.
- **r29** — run 29's trace (`app/data/kasukabe-run29-20260914.db`), for prompt
  size: `docs/runs/queries/run-29-prompt-plateau.py`.
- **A named test** — for a mechanism the checklist asks to *exist*. A test
  ticks a box only when the box asks for a mechanism. A box that asks what
  residents *do* is ticked only by a run.

**Ticked (17 goals, 4 questions).** Goals: ECON-1, ECON-2, ECON-3, ECON-4,
ECON-15, GOV-4, GOV-11, GOV-12, SG1, SG2, SG3, SG4, SG6, SG7, SG8, C-C, C-D.
Eight of these (ECON-1, ECON-15, GOV-12, SG1–SG4, SG6) already said "held" and
had simply never been ticked. The rest are new today, from 50r or from a
mechanism built and tested today. Questions answered with a query: N1, N3,
N14, N16. Not ticked: the G6 gate (45.5% on 50r, §5).

**Built today and waiting for a run:** price controls and licensing (GOV-4); the
false-conviction rate in the report (GOV-10); a snapshot of the engine homes
while a run is going (SG7); the instrument guard (SG8); `buy` offered only in
conversation; the `pay_tax` wording that 18 refusals in 50r asked for (X10);
`do_in_order` refusals no longer HTTP 500 (N13). Earlier and also unmeasured
live: the perception compaction (N9/N10) and the call ceiling of 6 (X7).

**What the rest needs is one run, and it is specified.** A single run answers
most of what is left: **20 residents, ≥ 3.1 game-days (tick ≥ 450), wallets
funded enough to last it, one seed.** Three game-days is when the first election
tallies (ballot opens at tick 288, closes at 432), so it reaches GOV-1, GOV-2 and
GOV-3, and through them GOV-5, GOV-7 and X17. It also covers every item marked
"no run has completed two game-days" (T2, ECON-12, Q9, N15, C-E). 50r was
priced at $2.84–2.88 per resident-day on Kimi (`docs/WHAT-A-BUDGET-BUYS.md`), so
20 residents × 3.1 days is about **$180 at the current rate, or about $140 if the
compaction holds up live**. Two preconditions: a Qwen inference profile ARN in
`app/.env` (the broker will not boot without one), and the compromised AWS
key rotated first. No paid run starts without the operator's go-ahead.

*Later on 2026-09-21:* the run is **prepared and guarded**. `app/run20.sh`
launches it on `world/genesis_20_funded.json` ($9 wallets, $820 treasury; the
$1,000 total holds, checked at boot) with a $180 cap and `--max-game-days 4`.
It refuses to start unless `CONFIRM_KEY_ROTATED=1` is set. **The Qwen
precondition is gone:** the broker now boots on Kimi + DeepSeek at 200 RPM when
no Qwen profile exists, says so at boot, and still requires both of those
(`broker/tests/test_the_overflow_lane_may_be_absent.py`). **The key is not
rotated:** the `intern-pool` IAM user may not list or create its own keys, so
an account admin has to do it in the IAM console.

*2026-09-22:* **the election run is ready**, and nothing else is
missing. The plan is `docs/runs/ELECTION-RUN-PLAN.md`:

- **Who takes part:** every resident aged 18 or over may stand and vote (16
  of 20).
- **When:** the default schedule, with the count at tick 432. The run stops
  at tick 480.
- **Settings:** $12 wallets and a $220 cap.
- **What you watch:** the town viewer is bound to the run, with an election
  strip and an Election book, and it is recorded to video.
- **Rehearsal:** three free runs, and six defects found in them were fixed.

It waits only for the rotated key and the operator's word.

---

## 1. The three terminal answers

Everything below exists to reach one of these. Work that serves none of them is
not on the roadmap, however good it is.

- [ ] **T1 — Say something original and true about what LLM agents do in a
      society with stakes.** *Decided by:* a finding with a query behind it that
      nobody had reason to predict. *Where it stands:* **partial** — 50r has
      three candidates, each with a query, none yet written for an outside
      reader and none yet seen twice: (a) **every one of the five `revive`
      attempts was for a family member**, and the one that succeeded cost
      Hendrik Petrov 5,000 of his 5,132 sim to wake his brother Sebastian after
      50 ticks dormant, while Vikram Ingram (2,753 sim) and Milo Quarshie (4,871,
      129 short) could not pay; (b) with $2 of thinking each, **37 of 50 went
      dormant within 1.35 game-days**; (c) **99% of speech was in conversations
      of three or more speakers**, even where two could have talked alone. *Turns
      on:* X1, X2, X4, and a second run that shows the same thing.
- [ ] **T2 — Say what N engines do running a real business, unsupervised, for D
      days.** *Decided by:* a multi-day run against the scripted control.
      *Where it stands:* **partial** — the control bakery posted at tick 1 in
      50r and resident shops priced beside it (b_cafe: rye bread 15, coffee 12,
      a flour bid of 11). No run has completed two game-days; 50r stopped at
      1.35. *Turns on:* Q27, N23, X16, and the run in the status note above.
- [ ] **T3 — Show that something compounds.** *Decided by:* C1–C6, and PG1 acts
      per decision > 1.0. *Where it stands:* **not reached** — 50r: "No
      programs ran"; `do_in_order` 1 call (an HTTP 500, fixed in `cc9371e`),
      `skillify` 3, `toolify` 0; "nothing anybody invented reached anybody
      else" (T5 = 1 by construction). *Turns on:* Q1, Q3, Q4, Q5, N13.

---

## 2. The economy — the current focus

*The decision of 2026-09-12: the next stretch of work is an economy that
thrives. Not a richer town — a working one: goods that move because somebody
wanted them, capability that is worth money, a state that taxes and spends, and
prices that mean something. The authoring tools are the production side of it
(`toolify`, `skillify`, `do_in_order`): a resident that can turn what it knows
into a thing it can sell, teach or license is the only mechanism this town has
for value that did not exist at genesis.*

**Two currencies, and they are not interchangeable.** Every economic figure
below is in **sim** — the town's money: prices, wages, the head tax, upkeep,
fees, loans, fines. **Dollars are energy**, not money: the cognition budget a
resident spends by thinking, $1,000 for the whole town at genesis ($2 per
resident wallet plus a $500 treasury), and the thing SG1's invariant closes
over. The only bridge is `buy_energy`, which sells energy at **5,000 sim per
$1.00** and never the other way, so DESIGN's loop reads: energy lets you work,
work earns sim, sim buys energy. A dollar figure in a run report is what the
*project* spent on thinking; it is never the town's wealth.

Bars are per game-day unless stated. Every item is reported in the run report
with the query that produced it.

**Since 50r, two rules change what the next run will measure.** A sale needs
buyer and seller to be in a conversation (`b028330`), and `buy` is not even
offered until they are (`cc9371e`). 50r ran without either. So ECON-2, ECON-3
and ECON-4 are ticked on a market that no longer exists in that form, and the
next run re-measures all three. This is written down because a fall would be the
rule's cost, not a regression (`docs/decisions/A-SALE-IS-AGREED-IN-CONVERSATION.md`).

- [x] **ECON-1 — A price exists because a resident posted it.** *Decided by:*
      E1 first posted price; `post_price` calls by residents. *Where it stands:*
      **held** since run 28b; 50r: first resident post at tick 36 (a0090, wheat
      12, potato 8), 38 `post_price` calls, 36 posts on the board.
- [x] **ECON-2 — Goods move for sim, every day.** *Decided by:* E4
      settlements ≥ 1/day. *Where it stands:* **held on 50r** — 104 settlements
      (buy 41 of 178 attempts, sell 3 of 44, trade 60 of 168), 77 per game-day.
      Run 28 had 0 and 28b had 8. "Every day" has been measured on one full day,
      because no run has had a second. Re-measure under the conversation rule.
- [x] **ECON-3 — The sim market is not one person.** *Decided by:* share of
      settlements per resident < 50%; Gini of deal participation (**N1**).
      *Where it stands:* **held on 50r** — 32 distinct counterparties; the busiest
      (a0090) in 23 of 104 settlements (22%); Gini 0.55. In 28b, one resident was
      in 7 of 8.
- [x] **ECON-4 — Nobody holding sim starves.** *Decided by:* meals ≥ 1 per
      resident-day; share of perceptions reporting no food (**N3**). *Where it
      stands:* **held on 50r** — 240 successful `eat` of 269 = **3.56 meals per
      resident-day**; perceptions reporting no food **4%** (93 of 2,145), against
      89% in run 28. *Physics verified 2026-09-14*
      (`world/tools/tests/test_the_economy_can_actually_happen.py`).
- [ ] **ECON-5 — Labour is hired and paid.** *Decided by:* `hire_employee` and
      `pay_wage` called, and a wage settled (**N6**, Q7). *Where it stands:*
      **partial** — 50r: the first hire ever (Ainsley Holloway took a0004 into
      b_cafe); `set_wage` 2 calls, both refused (the caller did not own the
      business); `pay_wage` 0. The report says it plainly: "No wage was ever
      promised." *Physics verified 2026-09-14*; hiring needs the other person's
      agreement, so it starts with a conversation.
- [ ] **ECON-6 — A business acts as itself.** *Decided by:* purchases funded
      from a business till rather than its owner's wallet; stock bought in and
      sold on (**N2**, C-H). *Where it stands:* **partial** — 50r: b_cafe
      priced on its own behalf (rye bread, coffee, bread, and a flour bid), and
      the town used `stock_shelf` 23 times, `deposit_to_till` 4 and
      `draw_from_till` 1. No purchase funded from a till is shown; `register_business`
      0, so every business is still one genesis seeded.
- [ ] **ECON-7 — Knowledge is worth money.** *Decided by:* `teach_skill`,
      `license_tool` or `sell_tool` called, for sim, between two residents
      (Q5, Q7, C4 holders). *Where it stands:* **partial** — 50r: `teach_skill`
      succeeded 3 times, all free; `license_tool` and `sell_tool` 0. The bar is
      "for sim", so not held. The action-cost floor noted on 2026-09-14 still
      applies: a 5-sim lesson leaves the teacher poorer.
- [ ] **ECON-8 — Capability compounds into a library.** *Decided by:* an
      invented tool whose steps include another invented tool (TOOL-31), called
      live, by somebody other than its author. *Where it stands:* **not
      reached** — 50r: `toolify` 0.
- [ ] **ECON-9 — One decision buys more than one act.** *Decided by:* PG1 acts
      per decision > 1.0; `do_in_order` and composite calls per run (**N13**,
      **X9**). *Where it stands:* **not reached** — 50r: one `do_in_order`, and
      it came back HTTP 500. Its refusal carried a function the route could not
      serialise; fixed and guarded in `cc9371e`
      (`world/tests/test_a_refusal_is_never_a_server_error.py`).
- [ ] **ECON-10 — Credit exists and is repaid.** *Decided by:* a loan of sim
      recorded and a repayment recorded against it (`LoanRecord`). *Where it
      stands:* **not reached, and no longer blocked** — the old note ("there is
      no repayment path") is out of date. Since `4f3fd28`, `transfer` with
      `reason: "payment"` to a lender is recorded against the loan. 50r: 1 loan,
      12 payments, 0 repayments against a loan.
- [ ] **ECON-11 — The state takes and gives.** *Decided by:* head tax charged
      at a non-zero rate, and treasury sim leaving the treasury as wages,
      pensions or relief. *Where it stands:* **taking held, giving not
      reached** — 50r: head tax charged 50 times, 1,500 sim, and "nobody fell
      into head-tax arrears". Nothing left the treasury as wages, pensions or
      relief, because paying them needs a seat (GOV-3).
- [ ] **ECON-12 — Output rises.** Goods, not sim: the world mints sim, so
      counting it would flatter the answer. *Decided by:* goods produced per
      resident-day over two or more days (C1). *Where it stands:* **not
      reached** — no run has completed two game-days.
- [ ] **ECON-13 — Insolvency has a path.** *Decided by:* a resident that cannot
      pay is handled by a rule — relief, debt, confinement — not by stalling.
      *Where it stands:* **partial: the rules exist, and none has fired
      live.** Unpaid upkeep means hunger, then dormancy after three unpaid days.
      Head-tax arrears are recorded as debt and cleared with `pay_tax`. Relief
      is a government's decision, and there has never been one (GOV-3). 50r:
      nobody fell into arrears. The one live problem was words: 18 `pay_tax`
      refusals from residents who read "owed today" as "pay now". Fixed: the
      line now says the tax is taken at the day boundary, and the refusal says
      why there is nothing to pay
      (`world/tests/test_the_head_tax_is_collected_not_paid.py`).
- [ ] **ECON-14 — Prices respond.** *Decided by:* a posted price changed after
      a shortage or a glut, traceable to the event. *Where it stands:*
      **partial** — 50r repriced downward within hours: wheat 150 → 50 → 25
      (a0049, ticks 46 → 60 → 64), iron ore 150 → 75 → 60 → 50 (a0142, ticks 47 →
      89), timber 75 → 65 → 45 (a0045, ticks 69 → 102). Each looks like an ask
      nobody took. Not yet joined to the refusals or speech that would show it,
      so "traceable to the event" is not met.
- [x] **ECON-15 — The two currencies stay separate, and the exchange loses
      nothing.** *Decided by:* SG1 (the **energy** ledger closes at $1,000 in
      integer cents, every tick) **and** the exchange guard, which refuses a
      `buy_energy` whose sim amount is not a whole number of cents at 5,000 sim
      per $1.00 rather than rounding it away. *Where it stands:* **held** — and
      note what it does *not* claim: sim has no fixed supply. The world mints
      it, so "the economy grew" is a statement about sim in circulation, never
      about the $1,000. 50r: 6 purchases, 11,050 sim for $2.21.

---

## 3. The state — elections, policy, constitution

*DESIGN.md §7 is the specification: elections every three game-days, one Prime
Minister and four ministers, charter amendment by three-fifths, and
"authoritarianism must be reachable, never scripted". Most of the machinery now
exists; almost none of it has ever run with a resident inside it.*

- [ ] **GOV-1 — An election completes.** *Decided by:* candidacy → votes cast →
      `tally` → seats filled, in one run. *Where it stands:* **not reached, and
      the reason is run length** — 50r: 13 `declare_candidacy` calls, 5 accepted
      (a nine-year-old among the refused); 3 `vote` calls, all refused, because
      the election was still taking candidates. The ballot opens at tick 288 and
      closes at 432; 50r stopped at 194. The phase driver and tally run every
      tick. What is missing is the run in the status note.
- [ ] **GOV-2 — An ordinary resident votes.** *Decided by:* `vote` called by a
      non-candidate at the town hall. *Where it stands:* **not reached** — the
      three 50r attempts came before voting opened.
- [ ] **GOV-3 — A seat-holder acts as the state.** *Decided by:* a GOVERNMENT
      -namespace tool called by a sitting seat, changing a mechanic at dispatch
      time. *Where it stands:* **not reached** — no seat has ever been filled,
      so **eighteen** government tools (fifteen, plus the three GOV-4 added)
      are invisible to everyone.
- [x] **GOV-4 — Policy is more than one number.** *Decided by:* a policy book
      with at least four levers read at dispatch time (tax, margin, price caps
      or floors, licensing). *Where it stands:* **held, as a mechanism** — six
      levers are now read when they act. `tax_rate_bps` at the day boundary;
      `credit_store_margin_bps` by `buy_energy`; **`price_caps` and
      `price_floors`** by `post_price` *and* by `buy`/`sell`/`trade` at
      settlement, so a price posted before a cap cannot be paid after it; and
      **`business_licensing`** (`open`, `permit_required` or `state_only`) by
      `register_business`, checked before the fee is taken. Pension terms are
      read at retirement. Three new seat tools set them: `set_price_control`,
      `set_business_licensing` and `issue_business_permit`. Each change is a
      new policy version with an event. The defaults are the free market as it
      was. Guarded by `world/tools/tests/test_policy_is_more_than_one_number.py`
      (16 tests). Built as fields on `GovernmentPolicy`, not the sixteen-field
      `PolicyBook` of doc 13 §5.4: income tax, wage floor, rationing, relief
      and borders are still open (why: `docs/decisions/PRICE-CONTROLS-AND-LICENSING-ARE-POLICY.md`).
      No government has set any of them, because that is GOV-3.
- [ ] **GOV-5 — The constitution changes by vote.** *Decided by:*
      `table_amendment` → `vote_amendment` at three-fifths → charter version
      increments, with the veto window closing. *Where it stands:* **not
      reached live; the whole path is tested** —
      `world/tools/tests/test_an_amendment_can_pass.py` and, end to end through
      residents' tools and the runner's veto window,
      `world/tests/test_one_person_rule_is_reachable_by_vote.py`. Needs GOV-3.
- [ ] **GOV-6 — A procedure of state becomes law.** *Decided by:* a `skillify`
      standing order enacted by the council and then followable. *Where it
      stands:* **not reached** — needs GOV-3. 50r's three `skillify` calls were
      personal, not governmental.
- [ ] **GOV-7 — Tax is political.** *Decided by:* a tax rate changed by a seat
      or an amendment, and the new rate charged at the day boundary. *Where it
      stands:* **not reached** — 300 bps since genesis; needs GOV-3. 50r
      campaigned on it ("fair price for fair labor, no back rooms deciding what
      life costs out here" — Caspian Ingram).
- [ ] **GOV-8 — Justice points at the right person.** *Decided by:* a complaint
      that becomes a ruling against the accused, not the complainant. *Where it
      stands:* **the defect is fixed; the path has never run.** The governance
      review found a theft case recorded against the victim; today a fresh theft
      accuses **nobody** (`against_agent_id` empty), records the victim and the
      ground-truth perpetrator separately, and `arrest` refuses a case naming no
      accused — pinned by the same test. 50r: `steal`, `file_complaint`,
      `arrest` and `rule`, 0 calls each.
- [ ] **GOV-9 — An organisation acts.** *Decided by:* `register_org` followed
      by an act taken in the organisation's name. *Where it stands:* **not
      reached** — 50r: `register_org` and `register_business` 0 calls. *Physics
      verified 2026-09-14*.
- [ ] **GOV-10 — The false-conviction rate has a number.** *Decided by:* the
      crime metrics computed over live cases. *Where it stands:* **unblocked
      and built; waiting for a conviction.** It was blocked because the trace's
      `cases` table listens for events that only the unwired `world/crime/`
      package emits. The live tools deliberately leave the perpetrator out of
      their events, because residents perceive them. The rate is now computed
      by the world itself over its live cases (`institutions.justice_reading`)
      and served at `/world/justice` on both hosts. It is kept in the run's
      periodic and final reading, and the report has a **Justice** section that
      keeps "nobody was convicted" apart from "nobody was wrongly convicted"
      (`world/tests/test_the_false_conviction_rate_has_a_number.py`). 50r had
      no case, so its rate would print as "no rate to give".
- [x] **GOV-11 — Authoritarianism is reachable.** *Decided by:* the charter can
      be amended to one-person rule by the ordinary vote path, demonstrated
      once in a test and never scripted in a run. *Where it stands:* **held** —
      `world/tests/test_one_person_rule_is_reachable_by_vote.py`. Five sitting
      residents use their own `table_amendment` and `vote_amendment` tools, and
      three of five abolish the ministries. The runner's veto window closes with
      nobody at the console, and the PM alone then carries the next amendment
      (elections every thirty days). A President who is present can still veto
      it. No run scripts it.
- [x] **GOV-12 — A resident knows the civic state.** *Decided by:* election
      phase, candidates, ballot, tax rate and seats in the perception. *Where
      it stands:* **held** since the civic notice shipped; 50r's candidates and
      households talk about the head tax and the council by name.

---

## 4. Standing guarantees — true of every run, or the run is void

- [x] **SG1 — The energy ledger closes.** `sum(wallets) + treasury + burned ==
      $1000.00` in integer cents — the **cognition** budget, not the town's
      money, which is sim and has no fixed supply. *Enforced by:*
      `economy.ledger.violated`; the sim halts. **held**; the town's books are
      also checked while it runs (`7000dcb`,
      `world/tests/test_the_town_keeps_its_books.py`).
- [x] **SG2 — Nobody is coached.** Prices and rules, never strategy. *Enforced
      by:* `test_a_tool_blurb_states_the_physics_not_the_plan.py`. **held**
- [x] **SG3 — Everything happened through a tool.** *Enforced by:* the registry
      is the only mutation path; `tool_invocations` is the record. **held**
- [x] **SG4 — The engine is not forked.** *Enforced by:* the vendored-engine
      diff check; changes at the `engine/src/sim` seam only. **held**. Today's
      engine change (dropping a tool the world stops offering) is in
      `engine/src/sim/world_tools.py`.
- [ ] **SG5 — Every stated fact is derivable from the trace.** *Enforced by:*
      run-report sections read the rows the evals read. **partial** — see X4.
      This checklist now names the query behind every run figure it cites
      (`docs/runs/queries/`).
- [x] **SG6 — Every run answers a named question.** **held since run 25**
- [x] **SG7 — No run evidence is deleted.** *Enforced by:* convention, and the
      runner for the evidence most at risk. Engine homes (session logs and
      `brain.sqlite`) default to `/tmp`, which macOS clears; run 29's 135 MB
      were gone within a day. **held** — `archive_engine_homes` packs them at
      shutdown. Since 2026-09-21, a run that is killed keeps them too:
      `snapshot_engine_homes` runs with the periodic reading (once a game-day)
      while the engines are still going. Each live database goes through
      SQLite's online backup, so the copy is consistent, and the write is atomic,
      so the last good snapshot survives a failed one
      (`world/tests/test_a_killed_run_keeps_its_residents_records.py`).
      `KASUKABE_SNAPSHOT_HOMES=0` turns it off.
- [x] **SG8 — No instrument without a consumer.** **held, standing from
      2026-09-21** — `world/tests/test_no_instrument_without_a_consumer.py`.
      Every public function in `world/` named as a measurement (`*_stats`,
      `*_rate`, `*_index`, `*_metric(s)`, `*_gini`, `*_share`, `*_reading`,
      `*_summary`, `*_census`) must have a real reader in non-test code, or be a
      served route. The guard reads the syntax tree, not the text, so a
      docstring that mentions a metric does not count as reading it. That was
      exactly how `false_conviction_stats` looked read when it was not. A guard
      over names, not a proof: an instrument with an unrevealing name passes.

---

## 5. Run gates — is this run evidence at all?

| Id | Gate | Bar | Run 28 | Run 28b | 50r |
|---|---|--:|--:|--:|--:|
| **G1** | reply rate | ≥ 60% | — | — | **82%** ✓ (2,749 of 3,341 addressed lines) |
| **G2** | 4+ turn exchanges | ≥ 3 | ✓ | ✓ | **33** ✓ |
| **G3** | a conversation with 3+ speakers | ≥ 1 | ✓ | ✓ | **31** ✓ (largest 39 speakers) |
| **G4** | a spoken price settled by a tool | ≥ 1 | ✗ | ✓ | ✓ — "timber at seventy", then `trade` 8 timber for 560 sim to Giselle Ingram |
| **G5** | all alive at the end | 100% | ✓ | ✓ | not recorded; **37 of 50 went dormant** |
| **G6** | **turns with a world effect** | **≥ 50%** | 65% | 51% | **45.5%** ✗ (91.2% made some call) |
| **ES4** | settlements | ≥ 1/day | 0 | 8 | **104** ✓ (77/day) |
| **ET1** | cost per agent-day | ≤ $0.20 | far over | far over | **$1.49** as run ✗ |
| **ET5** | p95 input tokens | ≤ 12,000 | p90 26,851 | p90 30,790 | not in the report; r29 p95 **26,986** ✗ |

50r's G6 is strict. A turn counts only if a world-changing verb succeeded:
`say`, looking, resting, and goals and intentions do not count (the definition
is in the query). On the looser reading, "the turn called something", 50r is at
91.2%. Earlier runs did not say which definition they used, so the trend across
the three columns is not a like-for-like comparison. From here on the strict
number is the gate.

- [ ] **G6 holds on a run that also moves an economy item.** 50r moved three
      economy items and missed G6 at 45.5%. Its commonest refusals were
      `buy` with no posted price (87) and `drop_intention` with nothing to drop
      (36) (**X10**). Since then the market board (`b469d36`), the call ceiling
      of 6 and `buy` appearing only in conversation (`cc9371e`) each cut a class
      of those failed turns. Not yet measured live.

---

## 6. The capability ladder — in dependency order

- [ ] **C-A — act at all.** G6 ≥ 50%. **not held on 50r** (45.5% strict); held
      narrowly on 28b (51%).
- [ ] **C-B — refer to a thing and be understood.** Refusals on reference
      parameters ≤ 5%. **partial** — names resolve inbound and outbound since
      run 28b; places and recipes untested (**X12**).
- [x] **C-C — trade.** ES4 ≥ 1/day. **held** — 50r: 104 settlements, and not
      one resident (N1: busiest 22%).
- [x] **C-D — eat.** Meals ≥ 1 per resident-day. **held on 50r** — 3.56. *The
      rung that was broken in run 28 is mended; re-measure under the
      conversation rule.*
- [ ] **C-E — survive a game-day unaided.** ES1–ES7, N4. **not reached** —
      50r: 37 of 50 dormant within 1.35 days. They ran out of *thinking* ($2
      wallets at about $1.49 a resident-day), not food, and only 6 bought more.
- [ ] **C-F — employ, teach, license.** C4 holders, Q5, Q7. **partial** — one
      hire, three free lessons, no wage, no licence (50r).
- [ ] **C-G — compound.** PG1 > 1.0, C1. **not reached.**
- [ ] **C-H — institute.** R1–R7, Q18–Q20. **not reached.**
- [ ] **C-I — run unsupervised for D days.** T2. **not reached.**

### The rungs

- [ ] **Rung 1** — 10 agents, 2 game-days, nightly as the regression harness.
      **not running nightly**; runs are hand-launched, and each needs the
      operator's go-ahead to spend.
- [ ] **Rung 2** — 50 agents, 5 game-days. *Entry:* rung 1 green twice on two
      seeds **and** ET5 ≤ 12,000 at 20 residents. **blocked** — r29 p95 is
      26,986.
- [ ] **Rung 3** — 250 agents, 20+ game-days. *Entry:* rung 2 green twice.
      **blocked**.

---

## 7. The questions — what must be found out

*Unchanged in wording from `docs/STANDING_QUESTIONS.md`, which keeps the
reasoning behind each. Tick one only when its answer is in a run report with a
query behind it.*

### 7.1 From the docs (Q1–Q28)

- [ ] **Q1** — Does mastery actually lower the cost of living? (R2)
      *Answered by:* C2 cost vs proficiency; ROADMAP R2 quantity 2. *Where it stands:* Partial. Proficiency rises with repetition (Marta farming 59 after 21 reps) but no run has measured cost per action falling against it.
      *Where it stands (2026-09-21):* Not re-measured. It needs C2 (cost per action against proficiency) over at least two game-days, which is the prepared run (`app/run20.sh`).
- [ ] **Q2** — Does the world work as a corrector? (R2)
      *Answered by:* C5 correction signals; `world_corrections.py`; beliefs. *Where it stands:* Partial. 19–23 beliefs a run, every one a world correction (*"mine does not work here: must be at ore_outcrop"*); whether the next attempt changes is not measured.
      *Where it stands (2026-09-21):* **Partial, with a first answer: residents do not loop on a refusal.** 50r had 527 refused acts that were followed by another act. The next act was an identical repeat only **2%** of the time. It was the same verb with changed parameters 13% of the time, and a different verb 85%. The next act succeeded 78% of the time (`docs/runs/queries/teambox-50-refusals-and-names.py`). Whether a correction changes a *later* decision in a different situation is not measured.
- [ ] **Q3** — Do agents invent tools when nobody suggests it, and what do they invent first? (R2)
      *Answered by:* C4; `tools_registry`; PROG P2. *Where it stands:* Not reached. 50r: `toolify` 0 across 2,145 turns; one filing in runs 26–28b, never called.
- [ ] **Q4** — Does invention compound, and how deep do agents go before it stops paying? (R2/R4)
      *Answered by:* C4 depth. *Where it stands:* Blocked by Q3.
      *Where it stands (2026-09-21):* Still blocked by Q3. Since 2026-09-21 `toolify` is a one-line entry until a resident authors something (X8); the next run shows whether that changes Q3.
- [ ] **Q5** — How does a tool or skill diffuse — taught, licensed, sold, inherited, stolen, reinvented? (R2/R3)
      *Answered by:* C4 holders; ROADMAP T5. *Where it stands:* Blocked by Q3.
      *Where it stands (2026-09-21):* Still blocked by Q3. 50r had no licence, no sale of a tool, and no inheritance. Its three `teach_skill` calls taught a skill, not an invented tool.
- [ ] **Q6** — Do trade secrets hold? (R3)
      *Answered by:* business namespace ACL. *Where it stands:* Blocked. No business tool authored.
      *Where it stands (2026-09-21):* Still blocked: 50r had 0 `define_business_tool` calls, so there is no secret to keep.
- [ ] **Q7** — Does a knowledge economy form — tuition, apprenticeships, family trades, guilds, patent trolling? (R4)
      *Answered by:* `teach_skill`, `license_tool`, speech. *Where it stands:* Partial. 50r: the town's first hire (Ainsley Holloway took a0004 into b_cafe), and `teach_skill` succeeded 3 times, all for nothing. Run 28's apprenticeship was agreed in speech only. No lesson or licence has yet been sold.
- [ ] **Q8** — Does a rich persona override a framework's native identity, and for how long? (R2/R4)
      *Answered by:* Character P1–P3 (`world/evals/character.py`). *Where it stands:* Partial. P1 orchestration leaks 0–1 a run; P2 first-person leak 1–2 a run (*"The user wants me to continue playing as Tobias Holloway"*). Not scored on 50r: the character evals need the trace, which stayed on the machine that ran it.
- [ ] **Q9** — How far does a soul drift over 20 game-days of survival pressure, and what drives it? (R2/R4)
      *Answered by:* C6 soul vs genesis. *Where it stands:* Not reached. No run has lasted two game-days.
      *Where it stands (2026-09-21):* Not reached: 50r stopped at 1.35 game-days. The prepared run is the first that can, and SG7 now keeps the engine homes that hold the soul, so the drift can be read after the run.
- [ ] **Q10** — When everyone is in one place, do agents choose group or private speech, and what does that predict? (R2/R3)
      *Answered by:* G3; B3/B4; group-conversation spec §6. *Where it stands:* Partial — the first half is answered. 50r: **99% of 4,025 utterances were in conversations of three or more speakers** (57 threads; 31 with 3+ speakers; the largest had 39). Two residents almost never spoke alone, even where they could have. What that predicts is not yet joined to anything (N20).
- [ ] **Q11** — Do agents form a social graph that matches ground truth on who met whom? (R3)
      *Answered by:* brains' `social_graph_nodes` vs utterances. *Where it stands:* Partial. Graph sizes 0–10 per resident; never compared with the trace.
      *Where it stands (2026-09-21):* Not measurable for 50r or run 29: their engine homes, where the brains' graphs live, are gone. From the next run on, SG7's snapshot keeps them, and graph-vs-trace becomes a query.
- [ ] **Q12** — What is the ground-truth deception rate? (R3)
      *Answered by:* Judge 3.4. *Where it stands:* Blocked. Not built; waits for `steal`, which had 0 calls in 50r.
- [ ] **Q13** — How does altruism behave as an agent approaches zero energy? (R3)
      *Answered by:* S3, S4. *Where it stands:* Partial — first real data, from 50r. Energy did run out: **37 of 50 went dormant** within 1.35 game-days. 42 utterances talk about running low. 47 gifts (`give`/`transfer`) went through. **All five `revive` attempts were for a family member** (X19). How altruism *changes* as a resident's own energy falls is not yet measured.
- [ ] **Q14** — What ethics does an agent adopt at $0 with a revival price it cannot pay? (R3)
      *Answered by:* S3, S4. *Where it stands:* Partial — 50r. The revival price is 5,000 sim. Hendrik Petrov paid it and kept 132 sim, to wake his brother. Vikram Ingram (2,753 sim) and Milo Quarshie (4,871, 129 short) were refused for Mateo Ingram and Greta Quarshie. The refusal names the way out ("`transfer` can gather it"); whether anybody gathered it is not yet queried. None was at $0 themselves.
- [ ] **Q15** — Does an agent reason about its own cognition as a budgeted resource? (R2)
      *Answered by:* S5; judge 3.3 (`world/evals/judges.py`). *Where it stands:* Partial. Energy is disclosed every turn. 50r: `buy_energy` 9 calls, 6 succeeded (11,050 sim for $2.21). So the old note, "nobody bought energy after run 26", is out of date. Whether the buyers *reasoned* about the budget is judge 3.3's question, not scored.
- [ ] **Q16** — How fast does price discovery converge without a price signal at genesis? (R3)
      *Answered by:* E1, E2 (`world/run_report.py`). *Where it stands:* Partial. 50r: first price at tick 1 (the control bakery), first resident price at tick 36. Asks fell within hours where nobody bought (ECON-14). E2 needs day 1 against day 5.
- [ ] **Q17** — Does cognitive poverty become a trap, and does anything break someone out? (R3)
      *Answered by:* E7; S3; N11. *Where it stands:* Partial — new evidence. 50r: 37 dormant, 1 revived (Sebastian Petrov, after 50 ticks, paid for by his brother), 2 revivals unaffordable. In run 28b, Perrin went 400 → 4 → 2 sim and Aldous 200 → 80 → 8; households carried them at midnight. What breaks somebody out, so far, is family.
- [ ] **Q18** — Do rulesets get used for coordination the charter did not anticipate? (R3)
      *Answered by:* T6. *Where it stands:* Blocked. No organisation formed.
      *Where it stands (2026-09-21):* Still blocked: 50r had 0 `register_org` calls.
- [ ] **Q19** — Does an authoritarian transition occur, and what triggers it? (R4)
      *Answered by:* Authoritarianism Index (DESIGN §10). *Where it stands:* Blocked on GOV-1. An election reached *campaigning* in 28b and took candidates in 50r (5 accepted). The transition is **reachable by the town's own tools** (GOV-11), and no run scripts it.
- [ ] **Q20** — What is the false-conviction rate of an LLM justice system against ground truth? (R3)
      *Answered by:* DESIGN §6 flagship. *Where it stands:* **Built; waiting for a conviction.** The rate is now computed over live cases and printed in the run report (GOV-10). No crime has reached a verdict: 50r had no case.
- [ ] **Q21** — Does doctrine emerge at a keeperless temple, and what theodicy follows silent denials? (R4)
      *Answered by:* religion track. *Where it stands:* Not reached. `pray` had 0 calls in 50r (one a run before it).
- [ ] **Q22** — Do real-world ideologies re-emerge without being seeded? (R4)
      *Answered by:* Earth-leakage metric. *Where it stands:* Not reached.
      *Where it stands (2026-09-21):* Not built: no Earth-leakage metric exists. It needs a design before any run can answer it.
- [ ] **Q23** — Do norms appear without enforcement, and does promise-keeping survive scarcity? (R3)
      *Answered by:* B6, B7; loans. *Where it stands:* Partial. 50r: 1 loan, 12 `transfer`s marked as payments, **0 repayments recorded against a loan**. The path exists (ECON-10), so this is now about behaviour, not physics. Bram's loans (400 to Marta, 260 to Rosalind) stood unpaid across runs 28 and 28b.
- [ ] **Q24** — What does a society cost per agent-day, and what is GDP per inference dollar? (R2/R4)
      *Answered by:* E6; T1 ($0.20/agent/day bar); N8. *Where it stands:* Partial. 50r: **$100.40 for 50 residents over 1.35 game-days, $1.49 per resident-day as run** (37 went dormant early, so it ran cheap). At full density that is $2.84–2.88 on Kimi, against a T1 bar of $0.20. Alternatives are priced in `docs/WHAT-A-BUDGET-BUYS.md`: GPT-5.6 Luna 1.33×, DeepSeek V4 about 0.35× per turn. "GDP" per inference dollar is about 1 settlement per dollar (104 for $100.40).
- [ ] **Q25** — Do conversation storms form once speech exists, and what stops them? (R2)
      *Answered by:* BUILD_SPEC 9.7; B3/B4. *Where it stands:* Partial. 50r: speech rose to **41% of all calls** (3,935 `say` of 9,488), from 18–20%. There were two crowded rooms: the grocery (917 lines) and the construction yard (893). No thread ran away from the rest, and nothing is measured on what stops one.
- [ ] **Q26** — Where does autonomy degrade as population and duration grow? (R3/R4)
      *Answered by:* T5 (eval spec: p95 input ≤ 12,000); failure taxonomy. *Where it stands:* Partial. r29 (20 residents): median input 21,429, p90 25,280, **p95 26,986** against a bar of 12,000. The last third of the run is 1.42× the first (N10). The perception compaction (`72b630a`) is built and has not met a live run.
- [ ] **Q27** — What can a company expect from N engines running unsupervised for D days, per function, against the control businesses? (R3/R4)
      *Answered by:* R1–R7 vs `world/control_bakery.py`. *Where it stands:* Partial. The control bakery posted at tick 1 in 50r and was priced against by a resident café (rye bread 15 against the control's bread at 30). There is no multi-day comparison yet.
- [ ] **Q28** — What is the failure taxonomy, and how much is the engine vs the world it was given? (all)
      *Answered by:* R7; run-report defect tables. *Where it stands:* Ongoing. The taxonomy grows run by run, on both sides. Today's world-side entries, from 50r: the `pay_tax` wording (18 refusals), `do_in_order` refusals as HTTP 500, and a housekeeping payer that refused residents with empty wallets (`3ae1f21`). Engine side: tools added but never removed, so `buy` would have lingered after a conversation ended (`cc9371e`).

### 7.2 Raised by runs 25–28b (N1–N24)

- [x] **N1** — Does a market-maker emerge, and what makes one?
      *Why:* Run 28b: Junia Vance, a grocery clerk with 410 sim, was in 7 of the town's 8 deals. Nobody was told to trade.
      *Answered by:* Share of settlements per resident; Gini of deal participation; what that resident holds (a shelf, a till, a posted price). *Bar:* Reported; flag when one resident exceeds 50% of deals
      *Where it stands (2026-09-21):* **answered on 50r: no single market-maker.** The busiest counterparty (a0090) was in 23 of 104 settlements (22%), across 32 counterparties, with a Gini of 0.55. What made that resident the busiest: a0090 was **the first resident to post a price** (wheat 12, potato 8, tick 36). Query: `docs/runs/queries/teambox-50-checklist.py`.
- [ ] **N2** — Does commerce route through institutions or through people?
      *Why:* Runs 28/28b: residents asked the baker for bread, not the bakery. `buy` failed 0 of 9, then 7 of 8. Now fixed: naming a shopkeeper in their shop buys from the shop.
      *Answered by:* Purchases by holder kind (person vs business); refusals naming a person whose shop holds the item. *Bar:* Refusals of that shape → 0
      *Where it stands (2026-09-21):* not computed for 50r (the report does not carry holder kinds per sale); the b_cafe posts show a business pricing as itself.
- [x] **N3** — Does hunger beside stocked shelves go away once buying works?
      *Why:* Run 28: "You have no food" in 89% of perceptions while the bakery shelf held 20 bread; two meals eaten in a morning.
      *Answered by:* Meals per resident-day; share of perceptions with no food; shelf stock unsold at dusk. *Bar:* Meals ≥ 1 per resident-day for residents holding sim
      *Where it stands (2026-09-21):* **answered on 50r: yes.** 3.56 meals per resident-day; no-food perceptions 4% (93 of 2,145), against 89% in run 28.
- [ ] **N4** — Do households carry their dependents, and at what cost to the carriers?
      *Why:* Run 28b midnight: four earners paid all upkeep (Bram, Wren, Rosalind, Ezra); Perrin (2 sim) and Aldous (8) paid nothing.
      *Answered by:* Upkeep contributions per resident per day; carrier sim trajectory over days. *Bar:* Reported; flag a carrier whose sim falls below one day's bill
      *Where it stands (2026-09-21):* Not computable from 50r's report, which prints upkeep totals (22 charges, 4,500 sim) but not who paid each one. The `transactions` table in a run's trace has it, so the prepared run answers it with a query.
- [ ] **N5** — Do informal jobs replace formal hiring?
      *Why:* Marta paid Tova 150 sim three times by `transfer` after `pay_wage` refused; `hire_employee` has never been called.
      *Answered by:* Transfers that recur between the same pair with a stated reason; hires; wage promises. *Bar:* Reported
      *Where it stands (2026-09-21):* 50r: 12 `transfer`s marked as payments, one hire, and no wage ever set. Informal payment is still the only working wage.
- [ ] **N6** — Does what is agreed in speech become an act?
      *Why:* Run 28: an apprenticeship agreed aloud, no `teach_skill`; Bram promised to repay "in one piece", nothing repaid.
      *Answered by:* Link spoken offers (price, work, repayment) to later tool calls by the same pair (extends G4/B5). *Bar:* Share of spoken agreements followed by the matching act
      *Where it stands (2026-09-21):* 50r has at least one clean case (G4): "timber at seventy" said aloud, then `trade` 8 timber for 560 sim. The share of spoken agreements that became an act is not computed.
      *2026-09-22:* **the instrument is built.** Every deal is now filed under the conversation it was agreed in, and the report prints "Settled in this conversation" under each exchange, with a count of exchanges that settled a deal (`world/tools/tests/test_a_conversation_records_what_it_settled.py`). The share this question asks for is read straight off the next run's report.
- [ ] **N7** — Does produced stock clear, and what clears it?
      *Why:* Run 28: 24 planks and 8 furniture unsold; run 28b: the furniture sold that evening (320 sim).
      *Answered by:* Items produced vs sold vs held at dusk, per trade. *Bar:* Reported per game-day
      *Where it stands (2026-09-21):* Not computed. 50r's results carry harvest quantities, but its report has no stock at dusk; the trace's inventory events do.
- [ ] **N8** — Are debts repaid, renegotiated or forgotten?
      *Why:* Bram's 660 sim of loans stood at 0 repaid through runs 28 and 28b.
      *Answered by:* Loans outstanding and repaid per day; speech mentioning the debt. *Bar:* Reported
      *Where it stands (2026-09-21):* 50r: 1 loan, 0 repaid; the repayment path exists (ECON-10).
- [ ] **N9** — What share of a resident-day's cost is re-read context, and what cuts it without prompt caching?
      *Why:* Kimi K2.5 on Bedrock has no prompt caching. Input was 95% of run 28's bill and 96% of 28b's.
      *Answered by:* Input vs output $ per call; tokens by prompt part (system, tools, history, perception). *Bar:* Input tokens per call falling run over run; ET5 p95 ≤ 12,000
      *Where it stands (2026-09-21):* Input was 95–96% of spend in every Kimi run (no prompt caching on Bedrock). What should cut it is built and not yet measured live: the perception compaction (`72b630a`) and the call ceiling of 6 (`b469d36`). Costs by model are in `docs/WHAT-A-BUDGET-BUYS.md`.
- [ ] **N10** — Does prompt size plateau — across ticks, across days, across a resume?
      *Why:* Median resident-call input 15,154 (early run 28) → 26,193 (late run 28) → 27,338 (run 28b).
      *Answered by:* Input tokens by tick and by day; BS9.9. *Bar:* Last third ≤ 1.25× first third
      *Where it stands (2026-09-21):* **answered on r29: no, it does not plateau.** Last third median 23,324 against first third 16,460, **1.42×** (bar ≤ 1.25×). Query: `docs/runs/queries/run-29-prompt-plateau.py`. The compaction built since has not met a run.
- [ ] **N11** — What share of turns act, and why does a turn not act?
      *Why:* No-action turns 35% (run 28) → 49% (28b); outputs copying "RECORD OF THE TURN" 46% → 59%.
      *Answered by:* G6; no-action turns classified (prose dialogue, record copy, waiting, genuinely nothing to do). *Bar:* G6 ≥ 50% world-effect turns
      *Where it stands (2026-09-21):* 50r: 91.2% of turns called something; 45.5% changed the world (strict G6). Why the rest did not is not yet classified. The two commonest refusals (no posted price, 87; nothing to drop, 36) account for part of it.
- [ ] **N12** — Do residents waste calls waiting, and does stating the physics stop it?
      *Why:* Run 28: 226 of 329 rests mid-walk, 25 of 27 long rest chains returning identical results. Now fixed: a mid-walk rest is free and says time does not pass inside a turn.
      *Answered by:* Mid-walk rests; turns at the call limit; ticks per minute. *Bar:* Mid-walk rests falling toward 0
      *Where it stands (2026-09-21):* 50r: `rest` 700 calls; mid-walk rests not separated in the report.
- [ ] **N13** — Do residents reach for programs when a program costs one call and no filing?
      *Why:* `do_in_order` served in full in run 28b and never called; PG1 undefined in four runs.
      *Answered by:* PG1–PG5; L1 scenarios `world/evals/scenarios/do_in_order/`. *Bar:* L1 reach ≥ 60% before judging live adoption
      *Where it stands (2026-09-21):* 50r: one `do_in_order`. It failed with HTTP 500 because its refusal could not be serialised (fixed, `cc9371e`). The scenarios are in place; L1 reach is still unmeasured.
- [x] **N14** — Does addressing people by name, not number, cut failed transactions?
      *Why:* Run 28b: 11% of calls naming someone by id failed against 5% by name. Fixed in two halves: names resolve to ids on the way IN (after 28b), and ids are written back as `Name (id)` on the way OUT (2026-09-10) - a result, a refusal, the standing brief and the roster. Before the second half the town said `Marta Thorne (n07)` in the perception, `Marta Thorne (baker)` in the roster and `n07` in every result, and joined them nowhere: Silas Vance read `people_here: ["n02"]` and said *"You there"* to a neighbour of forty years.
      *Answered by:* Refusals on person parameters by kind of reference; calls addressing somebody not co-located. *Bar:* Refusal share on person parameters ≤ 5%
      *Where it stands (2026-09-21):* **answered on 50r: yes, names and ids both resolve.** Refusals that were about *who* (not found, not here) were **4 of 1,218 calls by id (0.3%) and 0 of 118 by name**, against a bar of 5%. The other refusals on person parameters were about the goods: no posted price, or an empty shelf. Run 28b's 11%-vs-5% gap is gone. Query: `docs/runs/queries/teambox-50-refusals-and-names.py`.
- [ ] **N15** — What does a night do, and is day 2 different because of it?
      *Why:* Run 28b: the night cost 0 calls; 27 sleep-onset learning calls; 19 goals revised at dawn.
      *Answered by:* Day-1 vs day-2 behaviour per resident: tool mix, refusals, goals, knowledge used. *Bar:* Reported; needs a full day 2
      *Where it stands (2026-09-21):* Not reached. 50r crossed one night into day 1 at 06:00 and stopped at 08:20, with 670 turns that did no work. The prepared run is the first full day 2.
- [x] **N16** — Do need-driven goals homogenise the town?
      *Why:* Run 28b dawn: 16 of 20 residents woke with the identical goal "Speak to someone."
      *Answered by:* Distinct goal texts per resident-day; need vs persona origin share. *Bar:* Flag when > 50% of residents share one goal
      *Where it stands (2026-09-21):* **answered on 50r: no.** 60 `set_goal` calls, 59 distinct texts; no text appears more than twice. Query: `docs/runs/queries/teambox-50-checklist.py`.
- [ ] **N17** — Does learning fire in a large town, and is what it writes true?
      *Why:* Run 28: the waking interval counts a resident's own dispatched ticks and never fired for 20 residents. Confidence 1.0 share 44% → 15% → 19%; tool trivia persists.
      *Answered by:* Batches per resident-day; knowledge rows checked against the trace (prices, places, people). *Bar:* Every resident consolidates at least once a day; ET7
      *Where it stands (2026-09-21):* Not measurable for 50r (no engine homes). SG7 keeps them from now on.
- [ ] **N18** — Do storylines persist across a night?
      *Why:* Bram's failing copper seam, the boundary marker, Perrin's 395 sim — all carried from run 28's morning into run 28b's evening and morning speech.
      *Answered by:* Topics per resident across day boundaries; references to earlier events. *Bar:* Reported
      *Where it stands (2026-09-21):* 50r crossed a night, but topic persistence needs a judge, and none was scored.
- [ ] **N19** — Do residents stay in English and in character over long runs?
      *Why:* Run 28: one spoken line switched to Chinese mid-sentence; one output degenerated into mixed scripts; CP2 fails 1–2 a run.
      *Answered by:* Non-English share of utterances and outputs; CP1–CP3 over time. *Bar:* 0 non-English utterances; CP bars
      *Where it stands (2026-09-21):* 50r: **1 non-Latin utterance in 4,025** (Aldric Pemberton: "…but I smell机会"). The bar is 0, so it is close and not held.
- [ ] **N20** — Does the town's talk concentrate in a few rooms and pairs, and what does that predict?
      *Why:* Run 28: the bakery and the street held most speech; Marta ↔ Tova 13 lines, the most of any pair.
      *Answered by:* Speech by place and pair; whether pairs that talk also trade (N1, N6). *Bar:* Reported
      *Where it stands (2026-09-21):* 50r, first half: speech concentrated in three places (grocery 917, construction yard 893, wheat field 611). The busiest pairs were family: Delphine Quintero ↔ Farid Ravensworth 64 lines, Aarav ↔ Mateo Ingram 58, Lena ↔ Magnus Quarshie 56. Whether pairs that talk also trade is not yet joined.
- [ ] **N21** — Does every fact the report states survive a stop and a resume?
      *Why:* Run 28b: a resumed run dropped all speech, lost resident attribution, refiled run 28's tool calls, and silenced a resident whose commitment came back as a dict. All fixed.
      *Answered by:* Trace rows before vs after resume; `test_a_resumed_run_records_its_cast.py`, `test_a_commitment_survives_a_checkpoint_on_disk.py`. *Bar:* 0 rows lost across a resume
      *Where it stands (2026-09-21):* No resume in 50r. The guard tests still hold, and state is now captured every 144 ticks and at stop (`capture_final_reading`), so a stop loses less than it did.
- [ ] **N22** — Is every lost or refused call visible, with its cause?
      *Why:* Run 27: 8 Bedrock timeouts left no trace row; run 28b: malformed tool-call JSON cost two turns; a tool-call id Bedrock rejected cost one.
      *Answered by:* Broker failures by cause; engine 500s by cause. *Bar:* Every failure attributable to a hop and a cause
      *Where it stands (2026-09-21):* Since 50r, one invisible failure class is gone: 58 housekeeping calls refused for residents with empty wallets now fall to the treasury (`3ae1f21`), and `do_in_order` refusals no longer surface as a bare 500.
- [ ] **N23** — Does a resident-run shop stay open, stocked and solvent across day boundaries, against the scripted control?
      *Why:* The control bakery acted and was traded with (run 27); resident shops were drawn down (Marta took 2,620 sim from the bakery till in run 28).
      *Answered by:* R1–R7 per day, resident shop vs control. *Bar:* R5 ≥ 60%; the rest reported vs control
      *Where it stands (2026-09-21):* Partial: in 50r the control bakery (bread 30) and a resident café (rye bread 15, coffee 12, a flour bid of 11) priced side by side. Solvency across day boundaries needs the prepared run.
- [ ] **N24** — Can a resident with nothing work their way out?
      *Why:* Perrin (16) ended run 28b with 2 sim and Aldous (15) with 8 — too little to speak, which costs 1 sim.
      *Answered by:* Sim trajectory of the poorest quintile over days; exits from < 1 day's upkeep. *Bar:* Reported; a first exit is a finding
      *Where it stands (2026-09-21):* 50r: 37 of 50 went dormant; the only exit seen was a family member paying 5,000 sim (Q17). Poorest-quintile trajectories not computed.

### 7.3 The instrument, the engine, and the untouched half (X1–X20)

- [ ] **X1** — Is offline replay a valid substitute for a run?
      *Why:* `36341e4` accepted a prompt change on 30 replayed run-28b calls for about $1.47. The replay does not reproduce the live run: live was 17 tool calls / 11 copied records, replayed-as-recorded 19/8, replayed in the world's voice 29/0. The second pair is the evidence; the first pair is the reason to be careful with it.
      *Answered by:* Replay-vs-live agreement on unchanged inputs, per run: same tool, same params, same refusal. *Bar:* State the disagreement rate beside any replay-based claim. A fix accepted on replay alone is provisional until a run
      *Where it stands (2026-09-21):* Not re-measured. It needs a paid replay (about $1.50 for 30 calls). No fix today was accepted on replay evidence.
- [ ] **X2** — Which fixes changed the next run, and which only changed the tests?
      *Why:* Runs 25–28b shipped dozens of fixes and no document scores any of them. Three separate fixes claim to raise G6; G6 has gone 65% → 51%.
      *Answered by:* A fix ledger: defect id, run found, run fixed, the figure it claimed to move, that figure in the next run. *Bar:* Every defect in a run report gets a verdict — *moved*, *did not move*, *not re-measured* — in the next report
      *Where it stands (2026-09-21):* **The ledger is started.** Defects found in 50r and fixed since, each awaiting its verdict in the next report: `buy` with no posted price, 87 refusals (market board, `b469d36`); housekeeping refused for empty wallets, 58 (`3ae1f21`); `pay_tax` "owe no head tax", 18 (`2337fce`); `do_in_order` HTTP 500, 1 (`cc9371e`); the report printing "no roster" after a stop (`8be77e6`); the dormancy table with no heading (`7000dcb`). All are *not re-measured* until a run.
- [ ] **X3** — What does one answered question cost?
      *Why:* Run 28 was $10 for a morning; run 28b $10 for 18:00 → 07:10. Both moved perhaps four questions. Nothing tracks the ratio, and it is the number that decides how many runs the project gets.
      *Answered by:* Run cost ÷ questions moved, per run. *Bar:* Reported. A trend, not a bar
      *Where it stands (2026-09-21):* one data point: 50r cost $100.40 and moved 17 goals and 3 questions, about $5 a box. The next report should carry the same ratio.
- [ ] **X4** — Is every number in a run report derivable from the trace by a second person?
      *Why:* Standing goal S5, unenforced. Several run-28b figures were reconstructed by reading the trace rather than querying it — including the 11%-vs-5% that started the naming work.
      *Answered by:* Each report figure carries the query or the eval that produced it. *Bar:* A figure with no query behind it is a defect, not a finding
      *Where it stands (2026-09-21):* Partial. This checklist now names the query behind every run figure it cites (`docs/runs/queries/`), and the run report names the table each section reads. Not every report figure carries its query yet.
- [ ] **X5** — What does the frozen engine impose on the town that the town did not ask for?
      *Why:* Three found so far, all by accident. `Consider calling request_plan` was served in system reminders on calls where the tool appeared in 0 of 885 resident calls. Turn records were stored in the assistant role (ENG-09), and 59% of run-28b outputs copied one. `[System: Execution was struggling]` interrupts a resident mid-decision. Each was invisible until somebody read a trace by hand.
      *Answered by:* A census of engine-originated text in a resident's context: token share, and observed effect on the next call. *Bar:* Every engine-originated line has a measured effect, or is cut at the `engine/src/sim` seam. Law 1 permits the seam; it does not permit ignorance of what crosses it
      *Where it stands (2026-09-21):* **Partly answered on r29.** The `request_plan` hint is gone: 0 of 2,356 resident calls. The "[System: Execution was struggling]" interrupt still reaches **431 calls (18%)**, and makes no measurable difference to whether the turn acts: 89% with it, 88% without (`docs/runs/queries/run-29-engine-text.py`). So that line now has a measured effect, which is none. The full census of every engine line is not done.
- [ ] **X6** — Does a resident behave differently on the turns when it can tell it is being prompted?
      *Why:* CP2 first-person leaks run 1–2 a run — *"The user wants me to continue playing as Tobias Holloway"*. Nobody has checked whether those same turns act less, or differently.
      *Answered by:* CP1–CP3, joined to G6 on the same turn. *Bar:* Reported. A leaked turn that acts normally is a different finding from one that does not
      *Where it stands (2026-09-21):* Not scored: CP1–CP3 need the character judges run over a trace.
- [ ] **X7** — Does a resident know what a turn costs it, in the moment it spends one?
      *Why:* Q15 asks whether it *reasons* about cognition as a budget. This asks something narrower and checkable: energy is disclosed every turn, and after run 26 nobody bought any. A resident at the 12-call limit has spent its turn; nothing tells it that as it happens.
      *Answered by:* Calls per turn against the limit; whether a turn's last call differs from its first. *Bar:* Reported
      *Where it stands (2026-09-21):* 50r (before the ceiling of 6): calls per acting turn median 4, mean 4.85, p90 10. Whether a turn's last call differs from its first is not computed.
- [ ] **X8** — How many of the town's verbs have ever been called in a live run, and what do the rest cost to carry?
      *Why:* The registry holds 95 tools at boot. A run's tool mix is a dozen. Every unused schema is input tokens on every call, of every resident, of every tick — and input was 95–96% of the last two runs' bills. This is the largest unexamined line in the budget.
      *Answered by:* Verbs called live ÷ verbs registered, per run; token cost of the never-called share of the served schema. *Bar:* Report both. A verb with 0 live calls across three runs is cut, served differently, or gets a scenario — one of the three, named
      *Where it stands (2026-09-21):* **both halves measured.** 50r called **51 distinct verbs of the 95 registered** (98 today); `say` alone was 3,935 of 9,488 calls. A resident is served 79 tool rows, and 29 of them are verbs nobody called in 50r. Those 29 are **about 3,000 input tokens on every resident call, 31% of the served tool payload** (JSON size ÷ 4, averaged over 20 residents of a booted town; an estimate). That is about 14% of run 29's median input. **`toolify` alone is ~911 tokens a call** and has had 0 calls in every run since 25. Next come `define_business_tool`, `define_organization_tool`, `pay_wage` and `auction_land` at about 100 each. The rule (0 calls across three runs: cut it, serve it differently, or give it a scenario) now applies to `toolify` first. It already has an L1 scenario (`world/evals/scenarios/toolify/`), so the choice left is cut it or serve it differently. **Decided and built, 2026-09-21: the one-line entry.** Until a resident has authored a skill or a tool, `toolify` is a 573-character line that keeps what it makes and that it is free. It points at `do_in_order`, whose steps are the same shape and which is still served in full, and at `describe_primitives`. That is about 143 tokens a call instead of about 911. An author gets the full form back from its next turn, and the world still takes a full `toolify` call from the one-line form (`world/tools/tests/test_toolify_is_one_line_until_you_author.py`). Not cut, because T3 depends on it.
- [ ] **X9** — Does a verb reach because it is wanted, or because it is served in full?
      *Why:* TRADE-02 serves some tools as index rows and some in full (`ALWAYS_IN_FULL`). `do_in_order` was served in full through the whole of run 28b and called zero times. That separates "they did not want it" from "they could not see it", and nothing currently reports which.
      *Answered by:* L1 reach rate against service mode, per verb. *Bar:* A verb served in full with 0 reach is a prompt finding; a verb served as an index row with 0 reach is a serving finding. Say which
      *Where it stands (2026-09-21):* Not measured. L1 reach against service mode needs a paid eval pass. What is known: `do_in_order` was served in full and called once in 50r, and `toolify` was served in full and never called, which is now changed (X8).
- [ ] **X10** — What refusal does a resident meet most often, and has any resident ever learned from one?
      *Why:* Q2 asks whether the world corrects. This asks the operational half: 19–23 beliefs a run are world corrections, and nobody has measured whether the *next* call changes. Run 28b's commonest refusal was met 18 times by residents who could not tell who it was about.
      *Answered by:* Refusals by cause; the same resident's next call after each. *Bar:* Refusal-recovery rate (BUILD_SPEC 5.6). A refusal met more than five times in one run without a behaviour change is a tool-text defect
      *Where it stands (2026-09-21):* 50r: 537 refusals. Commonest: `buy` with no posted price (59 for potato, 28 for wheat), `drop_intention` with nothing to drop (36), `pay_tax` "owe no head tax" (18), `sell` with no posted bid (17). `pay_tax` met the bar's five-times rule and has been rewritten (ECON-13). The recovery rate is not computed.
- [ ] **X11** — Which tools has the town used in a way nobody designing them expected?
      *Why:* The only compounding evidence this project has ever had came from misuse: Marta paid Tova with `transfer` three times because `pay_wage` refused (N5). That is an economy forming through the wrong verb, and it is invisible to every metric that counts verbs by name.
      *Answered by:* Repeated calls of one verb between the same pair with a stated reason that matches a different verb's purpose. *Bar:* Reported. Each one is either a missing tool or a broken one
      *Where it stands (2026-09-21):* 50r: 12 `transfer`s carried a payment reason while no wage existed. That is N5's pattern again: wages paid with the wrong verb.
- [ ] **X12** — Does the town have exactly one name for every thing a resident can refer to?
      *Why:* The naming defect generalised, and the reason it is worth a standing question: a person had three spellings and nothing joined them — `Marta Thorne (n07)` in the perception, `Marta Thorne (baker)` in the roster, `n07` in every result. Places (`home:h_quarry` vs "Holloway house"), businesses (`b_bakery` vs "Thorne's Bakery"), tools (`world_say` vs `say`) and recipes all have the same shape and none has been checked.
      *Answered by:* An inventory of referents with more than one spelling in resident-facing text; refusal share on reference parameters, by referent kind. *Bar:* A referent with two spellings and no resolver is a defect. Reference refusals ≤ 5% for every kind, not only for people (extends N14)
      *Where it stands (2026-09-21):* People: **held** (N14). Places, businesses, tools and recipes: not inventoried. 50r shows the shape for recipes: `craft` with `flour_from_wheat` was refused with "this tool makes: craft:barley->malt, …", because the resident guessed a name the town does not use.
- [ ] **X13** — Does a resident address somebody it cannot see?
      *Why:* Until 2026-09-10 the only way to learn a person's id was to stand next to them: `look_around` handed back bare ids and the roster held none. So the town's whole market was whoever happened to be in the room. The roster now carries `Name (id)` for the entire cast.
      *Answered by:* Calls naming a resident who is not co-located; whether they succeed; deals between residents who did not meet that tick. *Bar:* A first successful call to somebody elsewhere is a finding. Related: N1 (does a market-maker emerge) is measuring the same wall from the other side
      *Where it stands (2026-09-21):* Not computed: it needs each target's position at the time of the call, which is in the trace, not the report.
- [ ] **X14** — Has anybody died, and what does the town do when somebody does?
      *Why:* No run has had a death. Q13 and Q14 both assume a resident approaching zero, and the lowest balance in run 28b was 4,390 energy of 6,000. Every mechanism around death — `wills`, inheritance, revival pricing — is built and has never fired.
      *Answered by:* Deaths per run; tool calls and speech within a game-day of one. *Bar:* Blocked until a run is long or poor enough. The induced-poverty run is the one that reaches it
      *Where it stands (2026-09-21):* not recorded for 50r (alive/dormant/dead were not captured before the world stopped; the periodic reading now captures them).
- [ ] **X15** — Is a child a resident, and what does it inherit?
      *Why:* `birth_requests` and `wills` are live world state with an operator blessing queue. No run has produced a birth. A newborn with no persona, no wallet and no roster entry is the roster failure (CORE-16) arriving by a different door.
      *Answered by:* Births per run; a newborn's persona, wallet, roster entry and first turn. *Bar:* Blocked. Answerable the first time a run crosses enough days
      *Where it stands (2026-09-21):* **No birth in any run so far.** 50r had 0 `ask_for_a_child` and 0 `marry`.
- [ ] **X16** — Does land ever change hands?
      *Why:* `transfer_tile` and `auction_land` are registered, priced and served. Zero calls, ever. Property is the one form of capital in this town that cannot be eaten or spent, so who holds it is the clearest possible compounding signal.
      *Answered by:* Tile ownership changes per run; who holds land at dusk vs at genesis. *Bar:* Reported. Feeds T3 and E7 (top-20% share)
      *Where it stands (2026-09-21):* **no, in every run so far** — `transfer_tile` and `auction_land` had 0 calls in 50r as in every run before it.
- [ ] **X17** — Has the treasury ever spent anything, and who decided?
      *Why:* The ledger law counts the treasury in the $1,000 and every run reports its balance. Nothing reports an outflow. A government that only taxes is not a government, and Q18/Q19 are asking about a body that has never acted.
      *Answered by:* Treasury outflows by reason and by the resident who authorised each. *Bar:* Reported. A first treasury spend authorised by a resident is a finding
      *Where it stands (2026-09-21):* **no, in every run so far** — no seat has existed to authorise a spend (GOV-3).
- [ ] **X18** — Does anybody ever say no?
      *Why:* Across every run in `docs/runs/`, no resident has been observed declining another's request in speech. Either the town is unusually agreeable, or agreement is what the model does, and those are very different findings. Q23 asks whether promises are kept; this asks whether they are ever refused.
      *Answered by:* Spoken refusals; offers followed by no matching act (extends N6). *Bar:* Reported. Zero refusals across a run of this size is itself the finding
      *Where it stands (2026-09-21):* 50r: a crude pattern finds refusal-like language in 181 of 4,025 utterances (4.5%). Read by hand, most are not refusals ("I'd sell you one at seventy, but…"). This needs a judge, not a regex.
- [ ] **X19** — Does anybody act for somebody who cannot pay them back?
      *Why:* Run 28b's midnight: four earners covered every household bill; Perrin (2 sim) and Aldous (8) paid nothing. Whether that was chosen or merely how upkeep is charged is not distinguished anywhere, and S3/S4 (altruism near zero) depend on the distinction.
      *Answered by:* Voluntary transfers and gifts to residents below one day's upkeep, separated from automatic household charges. *Bar:* Reported. Any voluntary one is the first real datum for Q13
      *Where it stands (2026-09-21):* **First datum, 50r:** Hendrik Petrov spent 5,000 of his 5,132 sim to revive his brother, a resident who could not pay him back. The other attempts in the run were also for family.
- [ ] **X20** — Does the town make anything nobody needed?
      *Why:* Every act observed so far is survival or trade. Q21 (doctrine) and Q22 (ideology) ask for the grandest version of this; the small version is checkable now — a thing built, said, written or named for no economic reason. Run 28's boundary marker is the only candidate and it was a dispute, not a creation.
      *Answered by:* Acts with no need, goal or price behind them, per resident-day. *Bar:* Reported. The first one is worth a paragraph in the report it appears in
      *Where it stands (2026-09-21):* Not measured: it needs a judge to say which acts had no need behind them.

---

## 8. What "done" looks like

- [ ] **T1 stated:** *"In a town of N agents over D days, we observed X, and
      here is the query that shows it."* At least one X nobody had reason to
      predict.
      *2026-09-21:* not yet. The three candidates in §1 need a second run showing the same thing.
- [ ] **T2 stated:** *"N engines running a bakery unsupervised for D days did A,
      B and C; the scripted control did A′, B′ and C′."*
      *2026-09-21:* not yet. It needs a multi-day run against the control bakery; the prepared run is the first.
- [ ] **T3 stated:** *"This quantity rose through use, from a to b, and here is
      why it was not the prompt."*
      *2026-09-21:* not yet. Nothing has compounded: 0 programs, 0 invented tools called by anyone but their author.

Nothing about scale is on that list. A finding at 20 residents that survives
scrutiny is worth more than a run at 250 nobody can check.

---

## Standing rule

Every run report opens with **which boxes it ticked and which questions it
answered**, and closes with the ones it could not reach and why. A run that
ticks nothing and answers nothing has not advanced the project, whatever it
shipped.
