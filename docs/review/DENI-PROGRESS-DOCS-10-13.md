# Progress on docs 10-13, and the true scope of docs 14-18

*Working state as of 2026-09-08. Companion to `SHARAN-PROGRESS.md` and
`STATUS.md`. Written so the work survives a context reset: everything
below is a claim about the tree that can be checked, not a summary of
intent.*

---

## 1. The scope was wrong for most of a session, and how

Docs 14-18 were tracked as **124 findings**. The real number is **154**.

The 124 came from a regex matching `### CORE-01`-style headings.
`18-growth-construction-expansion.md` declares its findings as bold
`**GROW-09 (P1) ...**` instead, so all thirty GROW findings were dropped
silently - and `33 + 30 + 33 + 28` happens to equal exactly 124, so the
total looked plausible. The error survived a full green regression and
was found only by re-deriving the list per document and seeing `GROW: 0`
against a document titled "growth".

**Rule that follows:** cross-check a derived work list against the source
document's own stated totals. `FINDINGS-REGISTER.md` says 268 findings
across docs 10-18; that is the number to reconcile against. A zero for a
whole category is a bug in the extractor, never a finding.

Correct totals, per document, declared-heading count:

| Doc | Prefix | Findings |
|---|---|---|
| `10-engine-seam-and-mind.md` | ENG | 27 |
| `11-world-tools-and-physics.md` | TOOL | 30 |
| `12-economy-and-life.md` | ECON | 32 |
| `13-governance-organisations-justice.md` | GOV | 25 |
| `14-world-core-runner-and-record.md` | CORE | 33 |
| `15-broker-trace-and-infra.md` | INFRA | 30 |
| `16-frontend-and-console.md` | UI | 33 |
| `17-evals-tests-and-docs.md` | EVAL | 28 |
| `18-growth-construction-expansion.md` | GROW | 30 |
| **Total** | | **268** |

Docs 00-09 declare **no** numbered findings. They are the README, the
run-23 evaluation, the state-of-the-system report, five specification
documents and the roadmap - the source material the numbered findings
cite. There is nothing to check off there.

---

## 2. Docs 14-18: all 154 closed

Commit `69d336b`, plus `4ca0bd9` for a collection error introduced by it.
Each finding carries a guard test that was observed failing against the
old code before the fix was kept - the standard `WORK-SPLIT.md` sets.

Two things in that commit are worth knowing about:

- **`4ca0bd9` fixes a defect `69d336b` created.** `69d336b` "restored"
  `app/game/tests/test_township_zone_bridge.py`, which had been *moved*
  to `app/tests/` and extended (15 test functions to the old 14). Two
  files with the same basename and no `__init__.py` is a pytest
  collection error, so `app/tests/` aborted with "Interrupted: 1 error
  during collection" - the same failure mode EVAL-17 had just fixed.
- Switches deliberately left **off**: `KASUKABE_REQUEST_REASONING`
  (INFRA-06 - needs a live call to establish whether Kimi on Bedrock
  honours `reasoning_config`) and `Genesis.caretaker_pm_agent_id`
  (GROW-12 - seating a PM nobody elected is a research decision, and
  `terms_served` stays empty so the regime metrics cannot mistake an
  appointment for a term).

---

## 3. Docs 10-13: audited, not assumed

114 findings. 71 had no id anywhere in the tree; five read-only agents
verified every one against the code, plus a spot-check of 20 of the 43
that *do* cite an id.

| | ECON | ENG | GOV | TOOL | Total |
|---|---|---|---|---|---|
| Fixed | 5 | 1 | 3 | 1 | 10 |
| Partly | 1 | 7 | 3 | 5 | 16 |
| **Open** | 19 | 11 | 6 | 7 | **43** |
| Blocked | - | 1 | - | - | 1 |
| Unclear | - | - | - | 1 | 1 |

The spot-check found three whose id appears in the code but whose defect
is still live: **ECON-03** (enum never widened), **ENG-21** (the id is
named in a comment justifying an unrelated change), **TOOL-25**
(`build_economic_context` / `render_economic_context_block` /
`HouseholdUpkeepView` still have no non-test caller).

It also found **GOV-04 had been made worse**: wiring the charter sink
fixed the loss and turned the handler-side duplicates into double rows on
*both* hosts, so the finding's own acceptance test began failing where it
had previously only lost events.

---

## 4. Fixed in docs 10-13 this session (15), all red-proofed

| Finding | What was wrong, and what it does now |
|---|---|
| `ECON-03` | The enum was the symptom. `build_transaction_row` writes `reason = transaction.reason.value` while `run_report._transactions` queries `where reason = 'daily_upkeep'` - a string no row ever held. The report asked for the upkeep and got nothing, which is how run 23 printed "no upkeep charged" over real money. Seven reasons added; the trace `tx_type` CHECK deliberately NOT widened (SQLite cannot ALTER a constraint, so new values would be rejected by every existing database - the "213 rows dropped in six ticks" failure `ensure_schema` documents). |
| `ECON-07` | `Ledger.settle_inheritance` was written, tested and called by nothing - `Will`'s own docstring names it. Money left circulation on death and the account stayed open, so `usd_total` kept counting a wallet nobody could spend. That is the invariant the halt gate reads. `apply_death` is now a coroutine; all eleven call sites updated. |
| `ECON-27` | `buy_energy` said "10,000 sim to the dollar" while `SIM_PER_USD` is 5,000 - ECON-05 halved it and the resident-facing text was never touched, so the tool that keeps an agent alive misquoted its own price by 2x. Also "Cost: 2 sim" against a schedule of 0, and "fixed rate" after ECON-30 made the margin real. All now derived from the constants. `check_debts` stopped promising arrears nothing records and a `transfer` route that refuses. |
| `ECON-29` | Every life fee went to the treasury, so a resident paying a pharmacist paid the government and the pharmacy earned nothing from the one service it sells. |
| `ECON-30` | `credit_store_margin_bps` had a setter, a validator, an API field and no reader. Now charged by `buy_energy` and kept by the treasury. |
| `ECON-31` | `run_daily_upkeep` skipped household-less residents while `charge_the_day_tax` billed them - two day-boundary jobs disagreeing about whether a person exists. Same defect as GOV-25 from the other side. |
| `GOV-04` | Six handlers emitted an event their own charter call already emits. Deleting them would have dropped `involved_agent_ids` (GOV-20's interrupt list, once `sorted(world.agents)`, which woke the whole town per candidacy) and the generic `agent_id` readers scope on - both moved onto the engine payloads first. |
| `GOV-10` / `TOOL-30` | `add_member`/`remove_member` existed since genesis with no tool, so `registry.py`'s ORGANIZATION check could only ever be true for the founder: every party, cult, religion and society was permanently a society of one. Added `admit_to_org` (founder-only, co-located, 2 sim) and `leave_org` (free). Roles, dues and an org treasury are still open. |
| `GOV-22` | `ComplaintLedger.file_complaint` had no caller, so `GET /governance/complaints` served a permanently empty list - "no grievances" and "no writer" were the same observation. `file_complaint` with `against_kind=government\|official` now writes it. |
| `GOV-23` | The licence fee was unpaid AND self-assessed: `fee_paid_sim` was a caller-supplied parameter defaulting to zero. Now read from the charter and debited, with the charter's own guard pre-flighted before money moves. |
| `GOV-25` | `is_resident = household_id is not None`, so an orphan or newborn silently lost the vote and was told it did not live here. Now roster membership. |
| `TOOL-24` | Index rows named the gate constant (`government_complex`) while `list_places` says "Kasukabe Government Complex". `display_place` folds it, following `ALSO_SATISFIES` for `town_hall`, which has no sector of its own - the gate a resident *votes* at, and the case the first draft missed. |
| `ENG-10` | Orphan tool-result repairs were silent, so an engine invariant break looked like a healthy run. Now counted and logged. |
| `ENG-15` | `search_memory` handed the raw question to FTS5 `MATCH`, where it is parsed as query syntax. Measured: `wheat` 1 row; `miller's wheat` syntax error; `wheat: damp` **no such column: wheat**; `what did the miller say?` syntax error. All swallowed into "Nothing in memory matches that", so three questions in four told a resident it had no such memory. |
| `ENG-18` | A world 500 was written to the resident's learning store as "the world refused `harvest`", and the learning pipeline consolidated it during sleep into a durable belief. An outage became a lesson that survived the outage. |

---

## 4b. Fixed after the four staged patches landed (2026-09-08)

All red-proofed the same way: guard written, fix reverted, **failure
observed and quoted below**, then restored.

| Finding | What was actually wrong |
|---|---|
| `ECON-09` (2nd defect) | **No pension had ever been paid.** `pay_the_pensions` looked up `world.wage_promises.get(agent_id)`, but that dict is keyed `"{business_id}|{employee_id}"` (`labour.py:112`), so the lookup could never hit and every retiree fell to the `pensions_without_a_salary` counter - while ECON-09 read as fixed, because the job was wired and running and doing nothing. **The existing test agreed with the bug**: it seeded `wage_promises[agent_id]`, the broken shape, not the one `pay_wage` writes. Red-proof: *"a 72-year-old who last earned 400 sim drew 0 instead of 100"*. Now takes the HIGHEST promise, so two jobs retire on the better one rather than on dict order. |
| `ECON-21` (completion) | The runner held a fourth private `DORMANCY_THRESHOLD_CENTS`. ECON-24 had already put the other three sites on one shared constant *precisely* so the number the law block quotes to the resident is the number the sweep enforces - and `soul_seed.py:208` puts it in the resident's own prompt. Raised the shared constant to 2 and deleted the private copy. |
| `ECON-18` | `registry.py` filled `skill_proficiency`, `trade_value_sim` and `holds_a_tool` and left `distance_tiles` at 0; `extract_quantity` returns exactly that field for `move`, so `per_unit_cost=2` multiplied by nothing and **every walk in town cost the 1-sim floor**. Red-proof: *"a walk across the whole town cost 1 sim"*. Units are TICKS despite the field's name - a street stride is 40 tiles/tick and the map is 140 wide, so raw tiles would bill a cross-town walk ~300 sim; ticks are also the figure `move` already quotes back as `estimated_ticks`. |
| `ECON-14` | `repay_head_tax_debt` had no caller, and could not have had one: the debt store lived on the runner, where no tool handler can reach it. `transfer` refuses the treasury (not an agent), so `check_debts` pointed residents at a tool guaranteed to refuse. The debt was real, compounding, itemised - and unpayable. Added `pay_tax` (free: a toll on repayment is how a debt becomes permanent). The **world** now owns the store and both roots ADOPT it, rather than each making its own. |
| `ECON-16` | `attach_eating` wrote `HungerState(consecutive_unpaid_days=0)` on every `agent.ate` - the MONEY counter - directly under a comment saying "eating is not a way to dodge the bill". A household could skip upkeep forever on one apple a day each. `HungerState` now has two counters: `consecutive_unpaid_days` (only paying clears it) and `days_since_meal` (eating clears it). Red-proof: *"the live subscriber wiped five days of arrears for one apple"* - and **the four unit tests stayed green**, which is why the wiring test existed. |
| `ECON-06` | Every capacity function - `work_capacity`, `effective_work_capacity`, `WORK_CAPACITY_BY_AILMENT`, `HUNGER_MILD_WORK_CAPACITY_MULTIPLIER` - was written, tested and read by nothing outside `upkeep.py`. A terminal patient harvested at full rate until the day it died. Now gated in `dispatch` for LABOUR tools only, **before** the charge (a refusal must be a no-op), naming illness vs hunger so the resident can act on it. Red-proof removed only the CALLER: 5 tests stayed green, the wiring test failed. |
| `CORE-16` (regression) | Not a new finding - a latent bug ECON-13 made live. `standing_brief` tested `if owed_by or owed_to` and then wrote BOTH keys, so a resident who was *owed* money but owed none rendered `owe: []`. Unreachable while `world.loans` was always empty; seeding the genesis debts turned it live on tick 0. |
| `GOV-21` (test) | `test_an_arrest_actually_moves_them_to_the_cells` asserted the gate constant `"town_hall"` - a string the movement system has no tile for - so it was satisfied by a prisoner `look_around` reported at `tile: null`. |

**Two "no caller" defects in this batch were caused by the same thing:**
state living on the composition root where `world/tools/` cannot see it.
`head_tax_debts` and `hunger_by_agent` both now live on `WorldState`, and
both roots adopt rather than construct. Worth checking the rest of
`GenesisRunner.__init__` for the same shape.

---

## 4c. The rest of the batch (2026-09-08)

| Finding | What was wrong |
|---|---|
| `ECON-20` (P1) | **Not on the open list, and live.** `format_volatile_line` has rendered `upkeep_due_sim` and `head_tax_due_sim` since it was written (`retrieval_depth.py:305-308`); `wallet_line` never set either, so both branches were unreachable. A resident could read its wallet, its burn rate and its runway and had no way to learn what was about to be taken out of it - then went hungry at the day boundary for a bill it was never shown. Red-proof: `KeyError: 'upkeep_due_sim'`. |
| `TOOL-25` | Two of its three limbs had already been closed by other work (`is_real_destination` is called from `civic.py:178`, `task_label` from `stakes_report.py` and `run_report.py`). The third is now closed by ECON-20: `build_economic_context` / `EconomicContext` / `HouseholdUpkeepView` have their first non-test caller. Used as the ASSEMBLER, not rendered as prose - the volatile line already carries these numbers, and a second prose block restating them every tick is duplicate tokens for every resident for every tick of a run. |
| `ENG-19` (handshake) | The agent that fixed ENG-19 wired the ETag into `world/main.py`, the **standalone** server, and handed the other half back. `run_genesis.py`'s embedded route is what every real run serves, so without this the saving existed only in a host no simulation uses - the signature defect of this codebase wearing a new hat. |

Fixed by parallel agents, each red-proofed in its own report: **ENG-14, ENG-17, ENG-20, ENG-22** (memory/learning/compaction/struggle in the sim seam) and **ENG-19, ENG-21** (conditional registry GET; a truncated monologue no longer buys a continuation). ENG-09 was taken by a third agent - `engine/src/sim/session_log.py` and `bounded_history.py` carry its work.

### Still open, and why

- **ENG-06** (P1) - **investigated in depth 2026-09-08; deliberately left
  open, and the reason is not "too big".** The diagnosis is exactly right
  and it IS the signature no-caller defect: loader, matcher, consumer,
  executor, reinjection and producer all exist and are all wired in
  `engine/src/bootstrap.py:663`; `engine/src/sim/pinned_boot.py:635` passes
  none of the four arguments. But wiring it was measured, at a real
  entry point, to be **a no-op and a hazard**:

  1. **The catalog can never match in the sim.** `catalog.py:153` keeps a
     skill only above `score > 0.1`, keyword score divides by the number of
     QUERY stems, and the query (`thread.py:2908`) is the whole perception -
     152 unique stems on the shortest real perception in the repo. Measured
     against a live engine: real perception -> `[]`; a short request ->
     matches. The query is chosen in FROZEN code, so the seam cannot fix it.
  2. **Passing `data_dir` hands a resident an assistant tool with an
     exec path.** `create_skill`'s served description tells the model to
     "Gather requirements via `ask_user` first" - the first name in
     `NOT_A_RESIDENTS_TOOL` - and its handler writes a caller-supplied
     `scripts` dict to disk verbatim, which `Utility.skill_execute`
     then `exec()`s in-process on match. Wiring it gives the model a
     write-then-execute-arbitrary-Python path inside the engine.
  3. **The only skills on disk are the ten assistant ones** (summarizer,
     email-composer, translator...). A naive fix offers a miner
     "email-composer".

  The finding's own note that this needs embeddings is **wrong** -
  `catalog.py:153` degrades to pure keyword by design - so a reader would
  check embeddings, conclude the precondition holds, wire it, and ship a
  no-op. What WAS done: `create_skill` added to `NOT_A_RESIDENTS_TOOL`
  (latent, no behaviour change today) so that removing it is a deliberate
  act rather than an accident.

  **The operator decision:** the town already has skill artifacts -
  `skillify` + `format_skill_playbook` (`retrieval_depth.py:957`, called
  `:1245`) - so "no artifact exists" is true of the ENGINE only. Option 1
  (status quo, zero risk) is recommended for the run. The real cognition
  saving is the finding's part (b), "a mastered skill runs under
  `keep_doing` with no model call", which lives entirely in
  `world/tools/state.py` and has nothing to do with the engine catalog.
- **ENG-24** (P1, M/L) - no goal / need / belief representation. The intention machinery it lists as "existing pieces" IS wired (`expire_intentions` from `run_genesis.py:7014`, `format_intention` from `retrieval_depth.py:1097`), but intentions are "what I am doing now", not goals, needs or beliefs. §5.3 describes a subsystem to build.
- **ENG-23** - blocked on the frozen `engine_core/` under ROADMAP Law 1.

Both P1s are architectural additions the findings themselves size M and M/L. They are the honest remainder, not an oversight.

---

## 4d. An engine test-isolation leak, newly VISIBLE and not newly created

Two agents independently reported the ~21 failures in
`engine/tests/test_compaction_orchestrator.py` as "pre-existing
test-ordering pollution". That is true but incomplete, and the complete
version matters, because the suite now LOOKS red where it did not before.

Established by bisection on 2026-09-08:

- `test_compaction_orchestrator.py` is **238/238 green in isolation**.
- **Any in-process sim-engine boot poisons it** - including
  `tests/test_sim_fake_broker.py`, which predates all of this session's
  work and produces the identical 21 failures when run first.
- It never showed before because `test_sim_*` sorts AFTER
  `test_compaction_*` alphabetically, so compaction always ran first.
  This session added `test_a_cut_off_sentence...`, `test_a_memory...`,
  `test_a_tick...` and `test_a_turn...`, which sort BEFORE it. The files
  did not create the leak; they moved the tripwire in front of it.
- Not config, and not the circuit breaker. Measured on both sides:
  identical `min_tokens=10000`, `max_tokens=40000`,
  `compaction_threshold=0.8`, `CircuitState.CLOSED`, identical inputs
  (48,000 estimated tokens against a 40,000 threshold) and an identical
  memory summary - yet `compact_if_needed` returns
  `strategy='none', was_compacted=False` after a boot and
  `strategy='session_memory', tokens_freed=37082` without one. The
  divergence is inside strategy selection.

Reproduce in two seconds:

    cd engine
    ./.venv/Scripts/python.exe -m pytest tests/test_sim_fake_broker.py         tests/test_compaction_orchestrator.py -q

**Deliberately not chased further.** It is test-only (the simulation runs
one engine per process), it lives in `engine_core/`, which is frozen
under ROADMAP Law 1, and it is outside docs 10-13. Renaming the new test
files to sort after it would hide a real defect, so they were left where
they are. This belongs in the register as its own finding.

---

## 4e. What the run found in eight minutes that 4,442 tests did not

The 50-agent series launched at 04:32 on 2026-09-08 and was dead by 04:40.
Two defects, neither in any finding, and the second is the one that
matters.

**1. `WorldState` has no `is_daytime`.** `ContinuingAction.satisfied`
(`world/tools/state.py:377`) has called `world.is_daytime()` since it was
written, for the `until={"dusk": ...}` stop condition. The method does not
exist. `is_daytime` exists as a FIELD on `GameTimestamp` and as a function
in `game/position.py`, so a reader greps it and finds it - just never on
the object that is asked for it.

No test reached it: every fixture in the tree builds a continuing action
with `units`, `ticks` or `item_count`. The town built one with `dusk`
inside its first game-hour. **This is the test-shaped hole, not the
mechanism-shaped one** - the branch table was never exercised end to end.

**2. One resident's exception ended the whole town.**
`dispatch_due_agents` fanned out with
`asyncio.gather(*(one(a) for a in due))` and no `return_exceptions`, so
the AttributeError went out of the gather, out of the tick, and out of the
runner. All fifty engines stopped. **The broker kept billing at
$10.77/hour for a town that was no longer being simulated** - the run
looked alive from the outside, and the only signal was the process count
falling from 106 to 4.

Now `return_exceptions=True`, with every failure counted in
`turns_lost_to_errors` and logged at WARNING. Counted, never swallowed: a
turn that vanishes quietly reads as a resident who chose to do nothing,
which is precisely the measurement this experiment exists to make. Same
rule the day boundary already followed ("one household must not take the
day down for the other sixteen") - the tick loop simply never got it.

**The lesson worth keeping:** a green suite of 4,442 tests said nothing
about whether the town could survive its own first hour. `boot()` is
covered; a RUN is not the same thing. Cheap insurance for next time: a
short real-broker smoke run before committing to five paid hours.

---

## 5. Staged, not yet applied

Written as patch scripts under the session tmp directory, held back only
because `run_genesis.py` and `world/tools/` are imported by tests the
parallel agents are running.

- **ECON-21** - the one-cent dead zone. The broker refuses dispatch below
  its ceiling estimate (1-2 cents at realistic token counts); the runner
  swept dormant only at zero. A resident holding one cent could not
  think, was not dormant, could not be revived (`revive` requires
  `is_dormant`), still owed upkeep and tax daily, and never started the
  24-hour clock. No death and no life. `DORMANCY_THRESHOLD_CENTS = 2`.
- **ECON-13** - genesis debts reach `world.loans`. `run_genesis.py` never
  read `opinions` at all, so every debt the persona generator created was
  prose; run 23 logged twenty `check_debts` calls and twenty empty
  answers. **The role is `"ower"`; the finding says `"owing"`** - taking
  the finding's word would have seeded nothing while looking correct.
- **ECON-15** - an unpaid upkeep bill becomes a debt. Also what makes
  ECON-16 fixable: the `agent.ate` subscriber clears
  `consecutive_unpaid_days` wholesale, so without an arrear the skipped
  bill vanishes the day a resident eats.
- **GOV-21** - a prisoner is moved to a place the map draws. `arrest`
  emitted `agent.moved` with `"town_hall"`, which has no tile, so
  `look_around` reported `tile: null`.

---

## 6. Still open in docs 10-13

**ECON:** 06, 08, 10, 12, 14, 16, 17, 18, 22, 24, 28
**ENG:** 06, 09, 14, 17, 19, 20, 21, 22, 23 (blocked - frozen engine), 24
**GOV:** 09
**TOOL:** 07, 18, 23, 25, 26, 29

The architectural ones, in rough order of consequence:

1. **ENG-09** - prose-only turns are structurally reinforced. The stored
   assistant turn is `full_text` narration, so every earlier "self" the
   model reads back is a monologue with no `tool_use` block. Direct cause
   of **152 of 349 sessions producing one paragraph and no action**, and
   the one defect that empties every downstream measurement regardless of
   what else is fixed.
2. **ECON-08 / ECON-22** - the `main.py` root has no economy, and two
   life-tick implementations have already diverged. ROADMAP T3 makes that
   root the only server.
3. **ENG-24 / ENG-06** - no goal, need or belief representation, and no
   skill artifact. "Agents that develop their own goals" has no
   substrate, and mastery costs as many tokens as ignorance.
4. **TOOL-07** - every resident's boot payload leaks all 11 government
   tools and every neighbour's invented tool by name, description and
   schema, against DESIGN section 2's explicit promise.

---

## 7. Method notes worth keeping

**The project's rule** (`WORK-SPLIT.md:35`, `STATUS.md:27`): *"guard
written, fix reverted, failure observed, then restored."* A test never
seen red is not evidence.

**The failure that recurs.** The commonest defect in this codebase is a
complete, tested function that nothing calls - and it appears *inside
guards written for it*. Three times this session a revert stayed green
because the test called the helper and not its caller: EVAL-15 (a source
grep passed over code that raised `NameError` on every tick), ECON-29,
ENG-15. **Every guard needs a wiring test that drives the production
entry point.**

**Read the code, not the finding's wording.** `"ower"` vs `"owing"`;
`self.ledger.usd_balance(agent_id)` vs the attribute the finding implies;
symbols here are named after what happened in a run, not after the spec
clause.

**Findings filed as blocked are usually actionable.** Every one re-read
from the source turned out to be doable - EVAL-15, EVAL-17/INFRA-11,
CORE-11, INFRA-06, UI-04. `engine/src/sim/` is the writable seam under
ROADMAP Law 1, and a wrong sentence in a docstring (`may_dispatch_to`
claimed the engine had no route to answer "are you busy") is how a
fixable thing stays filed as impossible.

**Do not edit while a regression runs.** Three false-failure incidents
this session, one of 37 tests. And do not wrap a long suite in `timeout`:
a killed pytest reports the wrapper's exit code, which reads as success.
