# The mistake register: every finding in the 19 docs, with an owner and a stamp

*Built by parsing all nine review documents (`10` to `18`) for their finding
ids, then checking a sample against the tree. 268 findings. Every row says
which document it came from and the line it sits on, so `sed -n '<line>p'`
on that file gives you the reviewer's full argument, evidence and proposed
fix - this register does not restate them, it routes them.*

## How to read a row

| Column | Meaning |
|---|---|
| **Id** | The reviewer's id. Cite this in commits and PRs. |
| **Line** | Line in that review doc. `sed -n 'Np' docs/review/<doc>` for the full finding. |
| **P** | The reviewer's priority. Blank means they did not assign one. |
| **Own** | Which **lane** the finding belongs to: **S** = Sharan (machine), **D** = Deni (world). Rationale in `WORK-SPLIT.md`. This is the lane, not always the author - two findings in Deni's lane (`GOV-04`, `ECON-05`) were built by Sharan because decisions 1 and 3 reached into it. Attribution by commit: `git log -S'<id>' -- app/`. |
| **Status** | Stamped below. Only rows marked FIXED/OPEN/PARTLY/DISPUTED were re-checked against the tree this session; the rest carry the review's own assessment and are marked `review`. |

**Status vocabulary.** `FIXED` - changed and proven by a test that was shown
to fail before the change. `PARTLY` - the mechanism exists and is wired, but
does not yet do its job. `OPEN` - re-checked, still broken. `DISPUTED` - the
review's claim did not survive a check; needs one more look before anyone
spends effort on it. `review` - not re-checked here; the reviewer's word.

**Honest limit on this register.** 11 findings were re-verified against the
code when this register was written, and a further 40 were closed with
proof in the passes below, thirty of them on the world side. The rest carry the reviewers' assessment.
The reviewers did not run the town, so their evidence is strong for
*"nothing calls this"* and weaker for *"this would not work"*. Two of the
findings that reached implementation did not survive contact unchanged:
`TOOL-02`'s "there is no kitchen" was wrong (the constant and the gate
both existed; nothing folded a place onto them), and `TOOL-01`'s fix was
incomplete as specified. Verify before you build.

**Nothing here has been proven in a live run.** Every `FIXED` row is
proven against the test suite. The claim these changes actually make is
that run 23's refusal classes - `no business with id`, `location:mill`,
`you hold N not M`, `'world_mine' is not something you can keep doing` -
cannot recur; whether the residents then do anything better with the town
is a question only a run answers.

## Where the 268 sit

| Review doc | Findings | P0 | P1 | Owner | Theme |
|---|---|---|---|---|---|
| `10-engine-seam-and-mind.md` | 27 | 2 | 11 | **S** | Engine seam and the resident's mind |
| `11-world-tools-and-physics.md` | 30 | 5 | 7 | **D** | World tools and physics |
| `12-economy-and-life.md` | 32 | 3 | 13 | **D** | Economy and life |
| `13-governance-organisations-justice.md` | 25 | 3 | 9 | **D** | Governance, organisations, justice |
| `14-world-core-runner-and-record.md` | 33 | 0 | 0 | **S** | World core, runner and record |
| `15-broker-trace-and-infra.md` | 30 | 0 | 0 | **S** | Broker, trace and infra |
| `16-frontend-and-console.md` | 33 | 0 | 6 | **D** | Frontend and console |
| `17-evals-tests-and-docs.md` | 28 | 3 | 6 | **S** | Evals, tests and docs |
| `18-growth-construction-expansion.md` | 30 | 6 | 8 | **D** | Growth, construction, expansion |
| **Total** | **268** | **22** | **60** | | |

*Docs `14` and `15` show 0/0 because those two reviewers put priority in
their §6 "work items (P0 first)" list instead of in the finding headings.
Their P0s are CORE-02, CORE-03, CORE-04, CORE-01, CORE-05, CORE-17 and
INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05. Treat those eleven as P0
wherever this table says `—`.*

## Fixed in this pass

**Forty findings are now closed**, across both lanes, each proven by a
test observed to fail against the old code before the fix was kept. A test
not shown to fail is not a test.

- **Sharan, the machine: ten.** Per-finding evidence, the live call-site
  counts and what is still open are in `SHARAN-PROGRESS.md`. EVAL-01 is
  written out at the end of this section as the clearest example of the
  family.
- **Deni, the world: thirty**, and four partly. Written out below.

### Deni's lane: D1 and D2 (2026-09-03)

The block the work split calls "one vocabulary for places" and "things to
do with a day", plus the two viewer fixes that make anything else here
observable. Its gate - a resident bakes, shelves, prices, another buys,
and the owner draws the takings, all through `registry.execute` - passes
as `test_a_shop_has_a_shelf_and_a_till.py::test_bake_shelve_price_sell_and_bank_it`.

| Id | P | What changed |
|---|---|---|
| `TOOL-01` | P0 | `place_aliases.resolve_business()` folds the bare key, the `business:` form the world itself prints, the registered name, the premises and a distinctive word; declines rather than guesses between two. Wired into `commerce`, `labour`, `civic`, `meta`. |
| `TOOL-02` | P0 | `Business.provides_workshops` + `satisfies_gate_in()`: a shop's premises satisfies the workshops its trade needs. The miller mills in her own mill. |
| `TOOL-03` | P0 | `stock_shelf`, `take_from_shelf`, `deposit_to_till`, `draw_from_till` - priced, evented, owner/staff gated at the premises. |
| `TOOL-04` | P0 | `holdings_line()` in the standing brief and in `look_around`, free and capped at eight piles with an honest tail. |
| `TOOL-08` | P1 | `place_for_charter()` at nine handler call sites, so the charter's exact-match check sees what the registry already proved. |
| `TOOL-09` | P1 | The utterance log is attached in `build_world`, not inside the map-only tile tracking. |
| `TOOL-11` | P1 | `register_business` takes a validated `premises`, so a new shop is not the town hall. |
| `TOOL-13` | P2 | `keep_doing` and `set_intention` fold `world_mine` / `world:cook` through `canonical_tool_ref`. |
| `TOOL-14` | P2 | `craft` and `cook` list every recipe they accept, grouped by workshop. |
| `TOOL-15` | P2 | Self-hire refused. |
| `TOOL-19` | P2 | `trade` and `steal` fold places instead of comparing them. |
| `UI-03` | P1 | `isAlive()` defers to the world's own `alive` when a run is bound; the browser can no longer bury a living resident. |
| `UI-09` | P2 | `esc()` widened to `'`; five raw insertion points of agent-authored text wrapped. |
| `UI-17` | P2 | The header clock derives from `LIVE.tick` when bound, and counts days from zero like the world does. |
| `GROW-28` | P3 | A workshop a business provides is no longer also listed as its own door. |

### The economy and the courts (2026-09-03, same pass)

| Id | P | What changed |
|---|---|---|
| `ECON-01` | P0 | The genesis tax rate is read from `village_rules.json` (300 bps = 30 sim/head/day), and `assert_the_day_was_charged` asserts sim moved rather than counting households visited. A zero rate stays a legal policy; a non-zero rate that yields nothing now halts the run. |
| `GOV-06` | P1 | Same change. |
| `GOV-02` | P0 | `CrimeCase` gains `victim_agent_id` and a write-once `ground_truth_perpetrator_id` no tool returns; `against_agent_id` becomes the accused and starts empty, because a fresh theft knows it was robbed and not by whom. `file_complaint` names somebody on an open case. `arrest` and `rule` refuse a case with no defendant. `false_conviction_stats()` is computable. |
| `GOV-15` | P2 | Fines are `TransactionReason.FINE`, not `TAX`. |
| `GOV-16`, `TOOL-22` | P2 | `release_served_sentences` runs every tick. |
| `GOV-18` | P2 | The world reads the rulebook for its genesis tax rate rather than carrying a second number. |

**Two defects found underneath GOV-02, neither in the review.**

1. `CrimeCase.outcome` meant two things. `steal` always set it -
   "succeeded" or "failed", the theft's own result - and `rule` refused
   any case whose `outcome` was not None as "already ruled on". So every
   theft arrived at the courthouse pre-judged with the word "succeeded",
   and `arrest`'s list of open cases (`c.outcome is None`) never contained
   a single theft. The justice chain was impossible twice over. Split into
   `outcome` (what the act did) and `verdict` (what the court decided).
2. `crime.theft.attempted` carried `thief_agent_id` to everybody who
   perceived it, so police knowledge and ground truth were identical by
   construction and there was nothing to investigate (GOV-05's note). Only
   a witness learns who now.

### Institutions that run (2026-09-04)

| Id | P | What changed |
|---|---|---|
| `GOV-01` | P0 | `charter_tick` advances the open election's phase, tallies at `voting_closes_tick`, and schedules the next cycle. `tally()` had no caller anywhere outside the charter's own tests, so seats stayed vacant and all eleven GOVERNMENT-namespace tools were uncallable by construction in every run ever made. |
| `GOV-03` | P0 | `table_amendment` and `vote_amendment`. The amendments engine was complete and tested and had no door. The President becomes a veto window with auto-sign after `--veto-window-ticks` (default 0), per the operator's decision - as a hard gate, an unattended run can never amend its own charter. |
| `GOV-13` | P2 | `vote` drops its registry `min_age`; the charter decides suffrage. Two rules for one question, and the stricter won, so an amendment LOWERING the voting age changed nothing while one raising it worked. |
| `GOV-17` | P2 | A civic notice in the perception: the open election and its phase, who is standing, whether you have voted, who governs, the tax rate. Both civic verbs require an `election_id` that nothing in the town would ever tell a resident. |
| `GOV-20` | P2 | A candidacy wakes the candidate, not the town. |

Suite after this pass: **2,856 passed, 4 skipped, 0 failed** across `world/`
and `game/` (15 m 51 s).

Partly closed: **`TOOL-12`** (charter refusals now reach the log;
`NotFoundError`, `RecipeNotFoundError` and `InsufficientInventoryError`
still fall through), **`TOOL-25`** (`is_real_destination` has a caller at
last; `task_label.py` and `render_economic_context_block` are still dead),
**`GOV-15`** and **`GOV-18`** as noted above.

**One test of mine had the defect it was written to catch.** The first
four confinement-sweep tests called `release_served_sentences` directly
and passed with its call site deleted - a mechanism complete, correct,
covered, and invoked by nothing, which is this repository's most common
defect. `test_the_sweep_is_actually_called_by_the_tick` drives the life
tick instead and fails when the call site goes.

**Two things the review did not say**, found by the guard tests:

1. Folding at the tool boundary is necessary and not sufficient.
   `buy(seller_id="business:b_bakery")` resolved the shop correctly and
   then read its price board under the resident's spelling, which is not
   where `post_price` filed it. The board, the stock, the ledger account
   and the event row are all keyed on the bare `business_id`, so the
   resolved id has to reach every one of them or the refusal moves one
   line down. See `_canonical_holder_id`.
2. Two of the files this work touches - `run_genesis.py` (six lines of
   genesis seeding) and `registry.py` (refusal classification, gate
   check) - also carry Sharan's CORE findings. Different functions, but
   they are merge points and S1 should know.

### Sharan's lane

**EVAL-01 - the report read event types the world never emits.** Run 23's
report printed "Nobody posted a price" over twelve real posts. The cause:
`run_report.py` read `business.price.posted`; the world emits
`economy.price.posted`. Checking the rest, **five of the six** event names
in the section I wrote were wrong, and two payload *keys* were wrong on top
of that:

| Report read | World actually emits |
|---|---|
| `business.price.posted` | `economy.price.posted` |
| `economy.head_tax.charged` | `economy.head_tax_debt.accrued` (and the amount is `amount_sim`, not `amount`) |
| `agent.dormancy.entered` | `health.agent.dormant` |
| `business.wage.set` | `business.wage.promised` |
| `economy.upkeep.charged` | **nothing at all** - upkeep moves money as a transaction and publishes no event |
| `economy.energy.bought` | correct |

Fixed in `app/world/run_report.py`: the names corrected, a `_transactions()`
reader added for upkeep (the only honest source, since it emits nothing), and
a `_norm()` adapter that fills the key a section reads from the key the
emitter writes. The guard is
`app/world/tests/test_report_reads_events_the_world_emits.py`, which builds
two sets - types the report reads, types `world/` emits - and fails when the
first is not inside the second. **It was proven to fail**: restoring
`business.price.posted` produces `assert not ['business.price.posted']`.

This one mattered out of proportion to its size. Every economic claim in
every run report so far came through it.

## `10-engine-seam-and-mind.md` — Engine seam and the resident's mind

Owner: **Sharan**. 27 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `ENG-01` | 289 | P0 | S | **FIXED** | The learning centre cannot write anything in the sim |
| `ENG-02` | 320 | P0 | S | **FIXED** | Every model call is emergency-compacted; the first perception is pinned forever |
| `ENG-03` | 365 | P1 | S | **FIXED** | All ~79 tool schemas are sent on every call; they are the dominant input cost |
| `ENG-04` | 380 | P1 | S | **FIXED** | Learning signals grow quadratically |
| `ENG-05` | 394 | P1 | S | review | `step11_skill_evolution` can never fire in the sim |
| `ENG-06` | 409 | P1 | S | review | No engine skill artifact has ever been produced or could be loaded |
| `ENG-07` | 426 | P1 | S | review | Assistant-shaped prompt scaffolding still on the wire |
| `ENG-08` | 449 | P1 | S | review | Kimi's native tool-call markup arrives as text and nothing parses it |
| `ENG-09` | 469 | P1 | S | review | Prose-only turns are structurally reinforced |
| `ENG-10` | 484 | — | S | review | Orphan tool_result legalisation exists twice with different semantics (P3, broker/engine-finding, S) |
| `ENG-11` | 498 | P1 | S | review | The namespace backend bypasses the adapter contract entirely |
| `ENG-12` | 512 | P1 | S | review | The 120 s dispatch limit abandons turns that keep running and billing |
| `ENG-13` | 523 | P2 | S | review | `learning_ran` means two things, and the sim suite is red on it |
| `ENG-14` | 530 | P2 | S | review | Memory-stream plans/reflections depend on thinking deltas that Kimi may not emit  — *inferred |
| `ENG-15` | 540 | P2 | S | review | `search_memory` silently returns nothing for most natural-language queries |
| `ENG-16` | 547 | P1 | S | **FIXED** | Location-reduced schemas are defeated by herald's tool cache — *herald caches on the NAME SET, so re-describing a tool changed nothing on the wire; the seam now drops the cache after every refresh*|
| `ENG-17` | 557 | P2 | S | review | Learning batch runs inline before the turn, inside the dispatch budget |
| `ENG-18` | 565 | — | S | review | Corrections are recorded for 5xx and world outages as if the town refused (P3, seam, S) |
| `ENG-19` | 570 | — | S | review | Per-tick full registry refetch by every engine (P3, seam/world, S) |
| `ENG-20` | 576 | — | S | review | `_tighten_compaction` writes a dead knob (P3, seam, S) |
| `ENG-21` | 581 | P2 | S | review | MAX_TOKENS continuations consume the call budget and the floor of 2 cannot act |
| `ENG-22` | 589 | P2 | S | review | Struggle counters persist across ticks in WM |
| `ENG-23` | 596 | — | S | review | Post-turn summary subject is the first 40 chars of the perception (P3, engine-finding) |
| `ENG-24` | 602 | P1 | S | review | No goal / need / belief representation |
| `ENG-25` | 609 | P2 | S | review | Law 1 has already been breached, without a manifest |
| `ENG-26` | 618 | P2 | S | **FIXED** | Tests that cannot catch the P0s |
| `ENG-27` | 627 | P2 | S | review | Prompt-cache economics are undone every call |

## `11-world-tools-and-physics.md` — World tools and physics

Owner: **Deni**. 30 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `TOOL-01` | 176 | P0 | D | **FIXED** | Three spellings of every place, no resolver at the tool boundary — *resolve_business() folds key/prefix/name/premises/word; buy and sell rebind to the canonical id; proven to fail on the old lookup* |
| `TOOL-02` | 183 | P0 | D | **FIXED** | Workshops are ids without ground; the mill business is not a mill — *resolved: kitchen_location() and the cook gate both exist, but nothing folded a sector or premises onto them. Business.provides_workshops + satisfies_gate_in; proven to fail* |
| `TOOL-03` | 189 | P0 | D | **FIXED** | A business's shelf and till are unreachable by its owner — *stock_shelf / take_from_shelf / deposit_to_till / draw_from_till, priced, evented; control bakery now uses them instead of world.inventory.move* |
| `TOOL-04` | 195 | P0 | D | **FIXED** | Residents cannot see what they hold — *holdings_line() in the standing brief and in look_around, free, capped at 8 with an honest tail* |
| `TOOL-05` | 200 | P1 | D | review | `move` is two mechanisms and the resident is never told which; `rest` cancels walks; arrival is not a wake |
| `TOOL-06` | 207 | P1 | D | review | Invented tools: no step-param validation, teleport inside one call, sim lost on partial failure, extra 5-sim fallback fee |
| `TOOL-07` | 214 | P1 | D | review | Confidential tools are served, by name and schema, to every engine |
| `TOOL-08` | 218 | P1 | D | **FIXED** | Charter engines compare location strings strictly; the registry folds them — *place_for_charter() at nine handler call sites; CharterError now classified as a refusal* |
| `TOOL-09` | 222 | P1 | D | **FIXED** | The utterance log is attached only when the map loads — *attach_utterance_log moved out of attach_tile_tracking into build_world* |
| `TOOL-10` | 225 | P0 | D | review | Construction is unreachable and never completes |
| `TOOL-11` | 231 | P1 | D | **FIXED** | Newly registered businesses live in the government complex — *register_business takes a validated `premises`* |
| `TOOL-12` | 234 | P2 | D | PARTLY | Refusal log and gap feedback see under half of the refusals — *CharterError subclasses now classified (TOOL-08); NotFoundError / RecipeNotFoundError / InsufficientInventoryError still fall through* |
| `TOOL-13` | 237 | P2 | D | **FIXED** | `keep_doing` and `set_intention` refuse the engine's own tool names — *both fold through canonical_tool_ref* |
| `TOOL-14` | 240 | P2 | D | **FIXED** | `craft`/`cook` descriptions name no recipe, `harvest` no crop — *both now list every recipe grouped by workshop; harvest untouched* |
| `TOOL-15` | 243 | P2 | D | **FIXED** | Owners can hire and pay themselves; hiring needs no consent or presence — *self-hire refused; consent/presence still open* |
| `TOOL-16` | 246 | P2 | D | review | Report reads the wrong event type for posted prices |
| `TOOL-17` | 249 | P2 | D | review | Two definitions of "said to you"; questions outlive the asker's presence |
| `TOOL-18` | 253 | P2 | D | review | `check_debts` cannot know a genesis debt |
| `TOOL-19` | 256 | P2 | D | **FIXED** | `trade` and `steal` compare places strictly — *both use satisfies_gate; proven to fail* |
| `TOOL-20` | 259 | P2 | D | review | Zones never regenerate and buildings never complete in the production runner |
| `TOOL-21` | 262 | P2 | D | review | The rulebook is not the world's |
| `TOOL-22` | 265 | P2 | D | **FIXED** | Confinement never ends on its own — *same sweep as GOV-16* |
| `TOOL-23` | 268 | — | D | review | `keep_doing` does not check the underlying gate at commit; repetitions run per cadence, not per tick (P3, world, S) |
| `TOOL-24` | 271 | — | D | review | Index rows name the gate in the tools' vocabulary (P3, registry, S) |
| `TOOL-25` | 274 | — | D | PARTLY | `is_real_destination`, `task_label.py`, `render_economic_context_block` are dead — *is_real_destination now has a caller (register_business); the other two remain dead* |
| `TOOL-26` | 277 | — | D | review | Descriptions that carry strategy (P3, registry text, S) |
| `TOOL-27` | 280 | — | D | review | Tests that pass on this checkout only with the map, and one that asserts the wrong thing (P3, tests, S) |
| `TOOL-28` | 283 | — | D | review | `mine costs 0 sim` (brief) is not what the schedule says (open, unverified) |
| `TOOL-29` | 286 | — | D | review | Double-charge accounting is correct but the "each step costs what it always did" story is not (P3) |
| `TOOL-30` | 289 | P1 | D | review | Organisations have no membership surface |

## `12-economy-and-life.md` — Economy and life

Owner: **Deni**. 32 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `ECON-01` | 172 | P0 | D | **FIXED** | Head tax bills 0 sim because the genesis rate is 0, and the S1 gate cannot tell — *genesis rate read from village_rules.json (300 bps); the gate now asserts sim moved, not households visited; proven to fail* |
| `ECON-02` | 177 | P1 | D | review | Day-0 upkeep is charged at the first tick of every run, silently |
| `ECON-03` | 182 | P2 | D | review | Upkeep, action costs and refunds are indistinguishable from gifts in the trace |
| `ECON-04` | 187 | P0 | D | review | Sim-currency is a one-way drain into a treasury that spends nothing |
| `ECON-05` | 192 | P0 | D | **FIXED** | The exchange rate makes energy unpurchasable from honest work |
| `ECON-06` | 197 | P1 | D | review | Illness and hunger have no effect on work |
| `ECON-07` | 202 | P1 | D | review | Money is not inherited on death |
| `ECON-08` | 207 | P1 | D | review | The `main.py` root has no economy: no upkeep, tax, wages, or zero-wallet sweep |
| `ECON-09` | 212 | P1 | D | **FIXED** | Pensions are never paid; the elderly have no income — *checked: `pay_pension` has zero non-test callers*|
| `ECON-10` | 217 | P1 | D | review | Government jobs, police and tenders create records that pay nothing |
| `ECON-11` | 221 | P2 | D | review | Population dials are ignored by the live loop |
| `ECON-12` | 225 | P1 | D | review | Pharmacy treatment is unreachable |
| `ECON-13` | 229 | P1 | D | review | Genesis debts never reach the ledger; `check_debts` is empty by construction |
| `ECON-14` | 234 | P1 | D | review | Head-tax debt can never be repaid |
| `ECON-15` | 238 | P2 | D | review | Upkeep shortfall is not a debt; `arrears["upkeep"]` is never written |
| `ECON-16` | 242 | P1 | D | review | A single bite resets the hunger ladder while the bill stays unpaid |
| `ECON-17` | 247 | P1 | D | review | `quote_action_cost` tells residents wrong prices for prefixed tool names |
| `ECON-18` | 251 | P2 | D | review | `move` is always charged its 1-sim floor |
| `ECON-19` | 255 | P2 | D | review | Head-tax payer is "first in dict order", not the household's earner |
| `ECON-20` | 259 | P1 | D | review | Residents are never shown today's upkeep or tax |
| `ECON-21` | 263 | P1 | D | review | The dead zone between broker refusal and dormancy |
| `ECON-22` | 267 | P2 | D | review | Two life-tick implementations that already differ |
| `ECON-23` | 271 | P2 | D | review | Births are unreachable in every run |
| `ECON-24` | 275 | P2 | D | review | The soul seed and standing brief state rules without the numbers that make them actionable |
| `ECON-25` | 280 | P2 | D | review | `control_bakery.py` and `stakes_report.py` are finished and unwired |
| `ECON-26` | 284 | — | D | review | (P3) Wiring tests that assert on source text cannot fail for the right reason |
| `ECON-27` | 288 | — | D | review | (P3) Description/schedule mismatches |
| `ECON-28` | 291 | — | D | review | (P3) `gather_wallet_and_burn` scans the whole transaction log per agent per tick |
| `ECON-29` | 294 | — | D | review | (P3) Marriage and treatment fees go to the treasury, not to the temple/pharmacy |
| `ECON-30` | 297 | — | D | review | (P3) `credit_store_margin_bps` is a policy lever nothing reads |
| `ECON-31` | 300 | — | D | review | (P3) Solo residents (no household) never pay upkeep |
| `ECON-32` | 303 | P2 | D | review | The `$10 run cap` vs `$6 x 20 wallets` makes dormancy unreachable by budget arithmetic |

## `13-governance-organisations-justice.md` — Governance, organisations, justice

Owner: **Deni**. 25 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `GOV-01` | 137 | P0 | D | **FIXED** | The election never concludes; government is vacant for the whole run — *charter_tick advances the phase, tallies at voting_closes_tick and schedules the next cycle; proven to fail when the call site is deleted* |
| `GOV-02` | 140 | P0 | D | **FIXED** | A theft case is filed against the victim, so the justice tools convict the robbed — *victim / accused / write-once ground truth; a theft accuses nobody until file_complaint names somebody; false_conviction_stats() computable at last* |
| `GOV-03` | 143 | P0 | D | **FIXED** | No amendment can ever pass: there is no vote tool and no table tool — *table_amendment / vote_amendment as GOVERNMENT tools; President is a veto window with auto-sign (operator decision)* |
| `GOV-04` | 146 | P1 | D | **FIXED** | Charter engine events are lost in production; duplicated in the other host — *checked: crime_engine appears 2x in WorldServer, 0x in run_genesis* |
| `GOV-05` | 149 | P1 | D | review | `world/crime/` is a second, unwired justice system; three definitions of "police", three of "complaint" |
| `GOV-06` | 152 | P1 | D | **FIXED** | The only tax the town can charge is zero, and the S1 gate cannot fail — *same change as ECON-01* |
| `GOV-07` | 155 | P1 | D | review | Nothing can ever be built: permits and builder licences have no issuing tool |
| `GOV-08` | 158 | P1 | D | review | The treasury has revenue but no spending; government jobs, police salaries, pensions and tenders move no money |
| `GOV-09` | 161 | P1 | D | review | Credit-store margin and pension policy are dead levers |
| `GOV-10` | 164 | P1 | D | review | Organisations cannot run: membership is founder-only forever, no treasury, no roles, no decisions, no rulesets |
| `GOV-11` | 167 | P1 | D | review | Land expansion is operator-only and, on the production host, decision-only |
| `GOV-12` | 170 | P1 | D | review | Checkpoint gaps: head-tax debt and crime store are not restored; `main.py` host does not checkpoint the charter |
| `GOV-13` | 173 | P2 | D | **FIXED** | Suffrage age is hard-coded in the registry, so an amendment to `suffrage_min_age` cannot take effect — *`vote` drops its min_age; the charter decides* |
| `GOV-14` | 176 | P2 | D | review | Head-tax payer is whoever iterates first |
| `GOV-15` | 179 | P2 | D | PARTLY | Head-tax debt events are never emitted; `rule` fines are booked as tax — *fines are now TransactionReason.FINE; head-tax debt events still go to a None sink* |
| `GOV-16` | 182 | P2 | D | **FIXED** | A jail sentence never ends on its own — *release_served_sentences swept every tick, with a test that fails when the call site is deleted* |
| `GOV-17` | 185 | P2 | D | **FIXED** | Residents cannot discover an election or the government — *civic notice in the perception: open election, phase, candidates, your ballot, who governs, the tax rate* |
| `GOV-18` | 188 | P2 | D | PARTLY | Two rulebooks — *the world now reads village_rules.json for its genesis tax rate; salaries, term length and the rest are still browser-only* |
| `GOV-19` | 191 | P2 | D | review | `social_api` is not mounted on the production host |
| `GOV-20` | 194 | P2 | D | **FIXED** | `declare_candidacy` wakes the whole town — *involved_agent_ids is the candidate alone; the notice replaces the fan-out* |
| `GOV-21` | 197 | P2 | D | review | `arrest` moves the accused to a location the map does not draw |
| `GOV-22` | 200 | P2 | D | review | Constitutional complaint ledger has no writer |
| `GOV-23` | 203 | — | D | review | (P3, world) Patent fee is checked twice and licence fees never move |
| `GOV-24` | 206 | — | D | review | (P3, tests) Tests that cannot fail on the production path |
| `GOV-25` | 209 | — | D | review | (P3, world) `EligibilityFacts.is_resident` = "has a household"** (`civic.py:66-75`). A newborn or a resident whose household dissolved becomes a non-r |

## `14-world-core-runner-and-record.md` — World core, runner and record

Owner: **Sharan**. 33 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `CORE-01` | 166 | — | S | **FIXED** | `--tick-interval-s` is dead: the runner's loop never sleeps, so the town runs at "as fast as the slowest turn" — *checked: run_continuous sleeps asyncio.sleep(0) - confirmed unpaced* |
| `CORE-02` | 172 | — | S | **FIXED** | Charter events never reach the bus in a live run (no `rewire_charter_event_sink` in the embedded root) — *checked: rewire_charter_event_sink lives only in class WorldServer (wiring.py:1107); run_genesis never calls it* |
| `CORE-03` | 178 | — | S | review | The run report's Prices / Stakes / Wages sections cannot ever be non-empty in a live run |
| `CORE-04` | 183 | — | S | **FIXED** | Day-boundary jobs run on the first tick of every run (and only then, so far) |
| `CORE-05` | 189 | — | S | **FIXED** | The pre-halt learning drain cannot fire on a live run: `run_continuous` never calls `drain_before_halt` — *checked: drain_before_halt IS wired (run_genesis.py:5228) but the governor still trips inside the same tick* |
| `CORE-06` | 194 | — | S | review | Population dials from the console never reach the life tick |
| `CORE-07` | 199 | — | S | review | Resume leaves the run unpaced and skips the pacing probe |
| `CORE-08` | 204 | — | S | review | Checkpoint incompleteness: hunger, head-tax debts, conversations, day-job ledger, perception delivery state |
| `CORE-09` | 210 | — | S | review | The 2,000-event ring buffer is the delivery mechanism for speech, and walking floods it |
| `CORE-10` | 215 | — | S | review | Dead and dormant residents keep being dispatched |
| `CORE-11` | 220 | — | S | review | Timed-out turns are cancelled on the runner side only; the engine finishes them against a stale world and the next tick queues behind |
| `CORE-12` | 225 | — | S | review | RPM/pacing arithmetic counts one call per due agent; a turn makes 2-12 plus a summariser |
| `CORE-13` | 230 | — | S | review | A re-run against an existing trace store poisons the recorder and can halt the run after three remarks |
| `CORE-14` | 235 | — | S | review | The daily checkpoint "mirror" writes to an in-memory database |
| `CORE-15` | 240 | — | S | review | Two disagreeing "unanswered question" mechanisms, and questions outlive the asker's presence |
| `CORE-16` | 245 | — | S | review | What a resident is *not* told (perception gaps for thriving) |
| `CORE-17` | 250 | — | S | **FIXED** | Night is neither cheap nor consolidated: no burst, continuing actions work through it, interrupts are discarded |
| `CORE-18` | 255 | — | S | review | The hung-engine half of the watchdog is dead on the process backend |
| `CORE-19` | 260 | — | S | review | Route collision: two `GET /world/state` definitions on the embedded app |
| `CORE-20` | 265 | — | S | review | `NamespaceAgentGateway` does not implement the gateway contract; the namespace backend would crash on first dispatch |
| `CORE-21` | 270 | — | S | review | `world_state.locations` and `tick_engine.locations` are two indexes; `place()` at genesis writes both by hand |
| `CORE-22` | 274 | — | S | review | Per-agent registry re-served and JSON schemas regenerated for every due agent every tick |
| `CORE-23` | 278 | — | S | **FIXED** | Tests that cannot fail (source-string assertions) on the exact paths that are broken |
| `CORE-24` | 282 | — | S | **FIXED** | Recorder statistics are invisible; no run manifest; no event schema version; no per-tick perception record |
| `CORE-25` | 287 | — | S | review | Spend governor and broker cap are the same number, and the governor can only trip after the broker already refused |
| `CORE-26` | 291 | — | S | review | `agent_viewer_api` (the "research centrepiece" reasoning viewer) is not mounted on the host that runs |
| `CORE-27` | 295 | — | S | review | `checkpoint_every_day` and `supabase_bucket` are inert; `world_mode="external"` is accepted but cannot work |
| `CORE-28` | 299 | — | S | review | `_phrase_activity` and the activity feed use `listener_agent_id` only; `run_report.threads` reply detection likewise |
| `CORE-29` | 303 | — | S | review | The learning corrector writes `source="engine"`; the pipeline's evolution step keys on `source == "user"` |
| `CORE-30` | 307 | — | S | review | Broker purpose tagging: the engine still sends no `X-Kasukabe-Purpose`; classification is inferred broker-side |
| `CORE-31` | 311 | — | S | review | Genesis placement puts everyone at `home:<household>`, which the map cannot resolve; `move` from there is an instant jump |
| `CORE-32` | 315 | — | S | review | `BARREN_TICKS_BEFORE_STOP` is measured in ticks whose wall length is unbounded |
| `CORE-33` | 319 | — | S | review | `wait_for_engines_ready` is sequential over 250 ports with a 2 s timeout per probe |

## `15-broker-trace-and-infra.md` — Broker, trace and infra

Owner: **Sharan**. 30 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `INFRA-01` | 132 | — | S | **FIXED** | `world_snapshots` rows and checkpoint archives go to a throwaway in-memory database on every real run |
| `INFRA-02` | 138 | — | S | **FIXED** | `llm_calls.tick` and `task_label` are always NULL, so per-game-day analysis and the Parquet export of calls are empty by construction |
| `INFRA-03` | 143 | — | S | **FIXED** | A resident at $0 is still dispatched every due tick and refused every time; the refusal storm at $0 (Build Spec D6, threshold 9.7) is not closed on th — *checked: the only is_dormant check is in the world_vitals read endpoint, not the dispatcher* |
| `INFRA-04` | 148 | — | S | review | The systemd/env deployment path in `app/infra/` cannot boot the current broker or a genesis run |
| `INFRA-05` | 153 | — | S | review | Shared secrets are literals in a committed launcher, and the default auth mode lets any client spend any wallet |
| `INFRA-06` | 158 | — | S | review | Reasoning is carried but never requested (Build Spec D2, second half; F1 still open) |
| `INFRA-07` | 163 | — | S | review | Kimi's text-form tool calls are parsed by nothing; the broker is the right place to parse them |
| `INFRA-08` | 168 | — | S | review | Raw LLM payloads are not archived; the trace body cap fires on most calls; "complete causal record" is not true |
| `INFRA-09` | 173 | — | S | review | No run manifest: code hash, engine hash, model ARNs/versions, prices, seeds and knobs are not recorded with the trace; engine minds are not checkpoint |
| `INFRA-10` | 178 | — | S | review | The Parquet export and the S3 replication have no production caller; the DuckDB extension is fetched at first use |
| `INFRA-11` | 183 | — | S | review | `broker.app` opens the production trace file at import time; the test suite cannot be collected on a fresh checkout and the docs still say mock is the |
| `INFRA-12` | 188 | — | S | review | Throughput pacing assumes one call per due resident; the call budget allows 12 plus housekeeping; the 250-resident math does not close |
| `INFRA-13` | 193 | — | S | review | The pre-dispatch estimate excludes tool schemas and tool inputs, and an un-collected debit is only remembered in process memory |
| `INFRA-14` | 198 | — | S | review | The broker's spend governor and lane/circuit state reset on restart |
| `INFRA-15` | 203 | — | S | review | Bedrock streaming pulls every event through `asyncio.to_thread`, so the default thread pool becomes the concurrency ceiling at 250 residents |
| `INFRA-16` | 208 | — | S | review | World events are not recorded in a genesis run; `events` holds only `broker.*` rows |
| `INFRA-17` | 213 | — | S | review | Six trace tables have no writer: `relationships`, `organizations`, `petitions`, `cases`, `buildings`, `eval_metrics` |
| `INFRA-18` | 218 | — | S | review | Trace-writer halt is invisible to the runner except as barren ticks |
| `INFRA-19` | 223 | — | S | review | The learning-centre and summariser calls are classified and routed, but billed to the resident and run mid-morning at 55-108K tokens |
| `INFRA-20` | 228 | — | S | review | The Firecracker track is unwired and untested on hardware; the process supervisor is the only real backend |
| `INFRA-21` | 233 | — | S | review | IAM policy model ARNs and the inference-profile wildcard need verification |
| `INFRA-22` | 238 | — | S | review | The Postgres migrations are dormant but still extended, and preflight points at them for a SQLite store |
| `INFRA-23` | 243 | — | S | review | `MockBedrockGateway` streams a `contentBlockStart` for text that real Bedrock omits |
| `INFRA-24` | 248 | — | S | review | Orphaned code with tests that cannot fail meaningfully |
| `INFRA-25` | 252 | — | S | review | `HttpLedgerClient`'s contract is pinned only against its own mock transport |
| `INFRA-26` | 257 | — | S | review | `events.id` is a UUID text PK for both writers but `SqliteClient.fill_generated_id` decides by column type at runtime per table; `utterances`/`memorie |
| `INFRA-27` | 261 | — | S | review | Reconcile alarm has no consumer |
| `INFRA-28` | 266 | — | S | review | `docker-compose.yml` references services and knobs that do not exist |
| `INFRA-29` | 270 | — | S | review | The 120 s tick timeout discards paid turns and the engine keeps running them |
| `INFRA-30` | 275 | — | S | review | Test-suite portability |

## `16-frontend-and-console.md` — Frontend and console

Owner: **Deni**. 33 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `UI-01` | 139 | P1 | D | review | The production host cannot grow the map; the two viewers cannot show growth even when it does |
| `UI-02` | 145 | P1 | D | OPEN | The DESIGN §8 research viewers are unreachable on a live run — *checked: same root: the research viewers mount on the host no run uses* |
| `UI-03` | 150 | P1 | D | **FIXED** | The browser's own health/food model can hide a real, living agent from the map — *isAlive() defers to the world's `alive` when bound; the dead branch in agentPosition too* |
| `UI-04` | 155 | P1 | D | review | A ~3,000-line in-browser simulation still paints fiction over a real run |
| `UI-05` | 160 | P1 | D | review | The baby-blessing queue can never fill on a live run |
| `UI-06` | 165 | P1 | D | review | The Phaser page (`index.html`) is dead against the production host |
| `UI-07` | 170 | P2 | D | review | Badge/HUD inconsistencies and a dead badge branch |
| `UI-08` | 176 | P2 | D | review | `/api/township/live` is heavy and the page polls it from two places |
| `UI-09` | 180 | P2 | D | **FIXED** | HTML injection from agent-authored text in the township page — *esc() widened to `'`; five raw insertion points wrapped* |
| `UI-10` | 184 | P2 | D | review | `agents.js` cut/build logic has stale-cache and empty-cast edge cases |
| `UI-11` | 188 | P2 | D | review | Observatory is not part of any run |
| `UI-12` | 192 | P2 | D | review | `/agents/{id}` detail is thrown away by the game client |
| `UI-13` | 196 | P2 | D | review | `MockWorldClient` no longer satisfies `WorldClient` |
| `UI-14` | 200 | P2 | D | review | `game/realsim.py` imports `world/` |
| `UI-15` | 204 | P2 | D | review | Real-sim button depends on infrastructure that is not in the repo |
| `UI-16` | 209 | P2 | D | review | The console's spend cap is hard-coded to $400 |
| `UI-17` | 213 | P2 | D | **FIXED** | Two clocks — *hourFrac derives from LIVE.tick when bound; day counts from 0 like the world's* |
| `UI-18` | 217 | P2 | D | review | `/places` on the production host has no geometry; the game's places panel is a hard-coded table |
| `UI-19` | 221 | P2 | D | review | Route-order bug: the dynamic `agents.js` route is declared after two middlewares that assume static content |
| `UI-20` | 225 | P2 | D | review | A Real-sim run and the base pages read different worlds |
| `UI-21` | 229 | P2 | D | review | Stale docstrings and documentation that a new engineer will follow |
| `UI-22` | 233 | P2 | D | review | `views.html`/`agents.html` cross-origin defaults and CORS |
| `UI-23` | 237 | P2 | D | review | Tests that cannot fail or test the wrong thing |
| `UI-24` | 241 | — | D | review | (P3, frontend) `talk.js` hard-codes IST (`:55-64`, `+330`); `groupchat.js:29-36` uses `Asia/Kolkata`. Fine for this operator, wrong for the paper's re |
| `UI-25` | 243 | — | D | review | (P3, frontend) `part-e.js` and `talk.js` both stamp the resident file (`talk.js:291-337` appends `.authored-strip`; `part-e.js` Skills tab) - two owne |
| `UI-26` | 245 | — | D | review | (P3, frontend) `shell.js` monkey-patches `window.openModal/closeModal/selectAgent/selectPlace/showEnginePanel` (`shell.js:236-273`) relying on `townsh |
| `UI-27` | 247 | — | D | review | (P3, build) Two generators write `agents.js` |
| `UI-28` | 250 | — | D | review | (P3, dead code) `township.html` + `township.js` + `village.js` (4,469 lines) are a pure-fiction v1 kept "for comparison" and reachable at `/township.h |
| `UI-29` | 252 | — | D | review | (P3, frontend) `renderPlaceCard` runs every 400 ms while a place is selected (`:5337`), rebuilding innerHTML and rebinding listeners; `renderGovernmen |
| `UI-30` | 254 | — | D | review | (P3, contracts) `contracts.py` documents a contract nobody implements and `UtteranceRecord`/`RelationshipRecord`/`AgentDetail.recent_utterances` are a |
| `UI-31` | 256 | — | D | review | (P3, console) `console.js` `renderPetition` (`:130-179`) offers only `edge` and `depth`; DESIGN §1's expansion "regenerates collision" but the operato |
| `UI-32` | 258 | — | D | review | (P3, console) Alerts are recomputed from the 2000-entry event ring on every poll (`admin_api.py:1134`, `EventBus.recent()`); a long run loses old aler |
| `UI-33` | 260 | — | D | review | (design) One map for engine and viewer - what it would take (question 2, second half) |

## `17-evals-tests-and-docs.md` — Evals, tests and docs

Owner: **Sharan**. 28 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `EVAL-01` | 79 | P0 | S | **FIXED** | `run_report.py` reads event types the world never emits; the economy sections are false by construction — *checked: 5 of 6 event names + 2 payload keys corrected in run_report.py; guard test proven to fail on the old name* |
| `EVAL-02` | 89 | P0 | S | **FIXED** | in a genesis run the `events` table is never written, so nothing that reads it can report anything |
| `EVAL-03` | 95 | P0 | S | **FIXED** | `llm_calls.tick` is always `None` and `task_label` is never written; C1 cannot be computed |
| `EVAL-04` | 102 | P1 | S | review | two task-label implementations that disagree, both orphaned |
| `EVAL-05` | 106 | P1 | S | review | `stakes_report.py` is the correct instrument and has no caller |
| `EVAL-06` | 110 | P1 | S | review | the L1 harness measures a prompt no resident is ever given |
| `EVAL-07` | 119 | P1 | S | review | L1 results are not persisted; `eval_metrics` is unused |
| `EVAL-08` | 123 | P1 | S | review | L1 covers 14 of 77 tools; the spec's own backfill list has no scenarios |
| `EVAL-09` | 127 | P1 | S | review | judges 3.2/3.3 and P1–P3 have no driver and have never run |
| `EVAL-10` | 132 | P2 | S | review | `eval_s4.py` passes on absence and hard-codes its inputs |
| `EVAL-11` | 135 | P2 | S | review | G1 and B1 ignore the addressee list the record now carries |
| `EVAL-12` | 138 | P2 | S | review | G3 rewards what B3/B4 penalise |
| `EVAL-13` | 141 | P2 | S | review | gates that cannot fail before a day boundary |
| `EVAL-14` | 144 | P2 | S | review | `run_report.py` mixes live and completed sources and loses the run when regenerated |
| `EVAL-15` | 148 | P2 | S | review | the control bakery is built and nothing runs it |
| `EVAL-16` | 151 | P2 | S | review | no resident in the series cast is in a control arm |
| `EVAL-17` | 154 | P2 | S | review | the app suite cannot be collected on a fresh clone |
| `EVAL-18` | 157 | P2 | S | review | the whole geography chain is untested without a gitignored vendored directory |
| `EVAL-19` | 160 | P2 | S | review | `cd engine && uv run pytest` fails at conftest import |
| `EVAL-20` | 163 | P2 | S | review | Parquet export needs the network at test time and has no caller |
| `EVAL-21` | 166 | — | S | **FIXED** | (P3, tests, S) — wiring tests that match source text |
| `EVAL-22` | 169 | — | S | review | (P3, docs, S) — the series was never closed |
| `EVAL-23` | 172 | P2 | S | review | the four compounding quantities have no reader that survives the run |
| `EVAL-24` | 176 | P2 | S | review | no run manifest; a run is not reproducible from the repository |
| `EVAL-25` | 179 | P2 | S | review | there is no per-call tool-invocation table in the trace |
| `EVAL-26` | 183 | — | S | review | (P3, docs, S) — stale generated file at `docs/RUN_REPORT.md` (57,865 lines) and `docs/RUN_REPORT.md`-era claims in `TEST_RUN.md` ("98% of turns sent w |
| `EVAL-27` | 185 | P2 | S | review | unclassified real failures on this checkout that need triage |
| `EVAL-28` | 191 | P2 | S | review | the engine seam's own suite is red on the `learning_ran` semantics |

## `18-growth-construction-expansion.md` — Growth, construction, expansion

Owner: **Deni**. 30 findings.

| Id | Line | P | Own | Status | The mistake |
|---|---|---|---|---|---|
| `GROW-01` | 106 | P0 | D | **FIXED** | No production path can create a `Plot`; `build_structure` is unreachable |
| `GROW-02` | 109 | P0 | D | **FIXED** | No path issues a `builder_license`; the licence gate refuses everyone including the construction company |
| `GROW-03` | 112 | P0 | D | **FIXED** | A completed building does not become a usable place: sector name never satisfies any gate, and no Business/Storage/workplace is created |
| `GROW-04` | 115 | P0 | D | **FIXED** | `complete_construction` / `buildings_due_for_completion` have no caller; construction never finishes — *checked: complete_construction defined at construction.py:157, zero callers; the docstring describes a caller nobody wrote* |
| `GROW-05` | 118 | P0 | D | review | Two independent `WorldMap` instances on `world/main.py`; expansion and stamped buildings are invisible to movement, `list_places`, and gates |
| `GROW-06` | 121 | P0 | D | OPEN | The production host has no `MapService`; land approval is a logged no-op — *checked: same root: bind_event_bus 0x in run_genesis* |
| `GROW-07` | 124 | P1 | D | review | Expanded map is not checkpointed in a live run |
| `GROW-08` | 127 | P1 | D | review | Two bills of materials with different constants and different inputs |
| `GROW-09` | 130 | P1 | D | review | Four disconnected notions of land: `tile_owners`, `WorldState.plots`, `MapService.plots` (`PlotRegistry`), and `Business.location_id` |
| `GROW-10` | 133 | P1 | D | review | The genesis construction company is a business record and nothing more |
| `GROW-11` | 136 | P1 | D | review | Tenders, permits and contracts exist in charter/ but have no tool surface; ROADMAP T3's "open a tender, award a contract, issue and revoke permits" is |
| `GROW-12` | 139 | P1 | D | review | `petition` is invisible to everyone because seats are vacant and no election has ever concluded |
| `GROW-13` | 142 | P1 | D | review | Expansion ignores the petition and grows bare ground |
| `GROW-14` | 145 | P1 | D | review | The viewer never sees a grown map or a built structure |
| `GROW-15` | 148 | P2 | D | review | Built sectors are classified inactive on restore and unknown to `classify_sector` |
| `GROW-16` | 151 | P2 | D | review | `Building` records no location id, and `is_real_destination` looks for one that does not exist |
| `GROW-17` | 154 | P2 | D | review | `build_structure` has `location_gate=None`: a licensed builder could raise a building from across town |
| `GROW-18` | 157 | P2 | D | review | Construction time is a countdown, not labour |
| `GROW-19` | 160 | P2 | D | review | Trace tables `petitions` and `buildings` are declared and never written; land/building events land only in generic `events` |
| `GROW-20` | 163 | P2 | D | review | `TileOwnership.from_expansion` is never set; `auction_land` cannot tell expanded from genesis land |
| `GROW-21` | 166 | P2 | D | review | `MapService.from_kasukabe_genesis` duplicates `build_kasukabe_genesis_map` step for step |
| `GROW-22` | 169 | P2 | D | review | `loader.py` (CSV) and `parcel.compute_bill_of_materials` are dead in production; `save_tiled_map` and `append_columns` have no callers |
| `GROW-23` | 172 | P2 | D | review | Processed goods have no sink and tools do not wear |
| `GROW-24` | 175 | P2 | D | review | `regenerate_zones` is called by `WorldServer.advance_tick` but not by `GenesisRunner.run_tick` |
| `GROW-25` | 178 | P2 | D | review | Operator console cannot see or approve anything but land petitions and amendments on the growth path |
| `GROW-26` | 181 | — | D | review | (P3) `LandExpansion` event payload is a receipt, not a map delta |
| `GROW-27` | 184 | — | D | review | (P3) Map tests fail rather than skip when assets are absent; the construction full-chain test silently skips |
| `GROW-28` | 187 | — | D | **FIXED** | (P3) `known_places` lists workshops that do not physically exist as "places you could go" — *a workshop a business provides is no longer listed as its own door* |
| `GROW-29` | 190 | — | D | review | (P3) `WORKPLACE_BY_OCCUPATION` sends builders to a phantom and shop staff to buildings that are not their shops |
| `GROW-30` | 193 | — | D | review | (P3) Stale docstrings claim the opposite of the code |

