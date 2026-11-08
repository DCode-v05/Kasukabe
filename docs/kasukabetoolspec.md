# Kasukabe tool specification: stakes, time, compounding

*Written 2026-09-02 against the `Real` branch. Companion to
`kasukabewhatismissing.md`. Every tool below is a `ToolSpec` in
`app/world/tools/primitives/`, priced by a row in
`app/world/ledger/action_costs.py`'s `SCHEDULE_TABLE`, dispatched through
`registry.dispatch()`'s existing order (validate, location gate, actor
requirements, charge, handler, record), and recorded as a `WorldEvent`.
Nothing here touches `engine/src/` outside `sim/`. Law 1 holds.*

**Decisions this spec is written to**

| Question | Decision |
|---|---|
| Continuing actions | Declared by the resident, through a tool |
| Wages | Paid by the employer, by hand, each payday. The world records the promise and the gap |
| Prices | Posted. Anyone may post: a business at its premises, an individual on what they hold |
| Burn rate | In perception every tick, as numbers only |

**Rules every tool obeys**

1. The description names the verb and its physics. It never suggests when
   to use it or why. `trade` and `sell` naming each other is the model.
2. Perception states facts in the past or present tense and stops. No
   advice, no "consider", no second half that offers a way out.
3. Every tool has a `SCHEDULE_TABLE` row, so it costs something and can
   be quoted by `quote_action_cost`.
4. Every tool emits a `WorldEvent` the `TraceStore` can write, with
   structured fields, so the run report can count it without parsing prose.
5. Every tool ships with a test named for the behaviour it guarantees, and
   one that fails if the tool is registered but not priced.
6. The gate map for reports comes from the specs, never from
   `/tools/registry`.

---

## Part A. Stakes

### A1. `buy_energy`

**What it does.** Converts sim-currency in the caller's hand into dollar
credit in the caller's own wallet, at the ledger's fixed rate
(`SIM_PER_USD`, **5,000 sim to $1** since the 2026-09-04 re-rating; read
from the constant, never retyped). Closes the survival loop.

| | |
|---|---|
| Namespace | public |
| Params | `sim_amount: int > 0` |
| Location gate | the government store (`LOCATION_GOVERNMENT_STORE`, the same place `list_places` already names for this) |
| Actor requirements | alive, not dormant |
| Ledger | debit caller sim; credit caller USD at `sim_amount // SIM_PER_USD_CENT` cents; the sim goes to the treasury, so the $1000 invariant holds |
| Cost row | `FEE`, base 0. The exchange is the price |
| Refuses when | `sim_amount` below one cent's worth; insufficient sim |
| Event | `economy.energy.bought` with `agent_id`, `sim_amount`, `usd_cents`, `usd_balance_after` |
| Renders | the caller's next volatile line shows the new wallet and runway (see A6) |

**Why the gate stays.** DESIGN.md says "bought at the government store, in
person". Walking there costs a tick and a decision, and where residents
choose to spend those is data.

**Test.** `test_buying_energy_moves_sim_to_treasury_and_dollars_to_the_buyer`;
`test_energy_cannot_be_bought_from_the_bakery`;
`test_ledger_invariant_survives_an_energy_purchase`.

### A2. `revive`

**What it does.** Pays `REVIVAL_COST_SIM` (10,000) from the caller to wake a
dormant resident. The fee converts to `REVIVAL_USD_CREDIT_CENTS` in the
revived resident's wallet, per `life/constants.py`, which already pins both
numbers and has no tool.

| | |
|---|---|
| Namespace | public |
| Params | `agent_id: str` |
| Location gate | the dormant resident's current location. You revive someone by standing over them |
| Actor requirements | alive; target must be dormant, not dead |
| Ledger | debit caller `REVIVAL_COST_SIM` (5,000 sim) to treasury; credit target $1 of USD runway at `SIM_PER_USD` |
| Cost row | `FEE`, base 0 |
| Event | `life.revived` with `payer_id`, `revived_id`, `ticks_dormant`, `sim_paid` |
| Renders | to the revived resident on its next tick: "You were dormant for N ticks. {name} paid to wake you." To the payer: the transfer in its ledger line |

**Test.** `test_revival_wakes_a_dormant_neighbour_and_charges_the_payer`;
`test_a_dead_resident_cannot_be_revived`;
`test_reviving_yourself_is_impossible_because_the_dormant_cannot_act`.

### A3. `post_price`

**What it does.** Publishes a standing offer to sell, and optionally to buy,
one item at a fixed price. A business posts at its premises and the price
is visible to anyone standing there. An individual posts on their own
inventory and carries the offer with them; it is visible wherever they
stand. The post stands until changed or withdrawn.

| | |
|---|---|
| Namespace | public |
| Params | `item: str`; `ask_sim: int > 0 \| None` (what you sell at); `bid_sim: int > 0 \| None` (what you pay for it); `on_behalf_of_business: str \| None` |
| Location gate | none for an individual. For a business, the business's own location, and the caller must be its owner or a granted employee |
| Actor requirements | alive |
| Cost row | `TRADE`, base 1 sim. A sign costs a coin |
| Refuses when | both `ask_sim` and `bid_sim` are null (use `withdraw_price`); business named and caller not owner or employee |
| State | `WorldState.price_board[holder_id][item] = PricePost(ask, bid, posted_tick, poster_id)` where `holder_id` is a business id or an agent id |
| Event | `economy.price.posted` with `holder_id`, `poster_id`, `item`, `ask_sim`, `bid_sim`, `previous_ask`, `previous_bid` |
| Renders | in `look_around` at a business: "Prices here: bread 30 (buys at 24), flour 8." Beside a person in `format_visible_agents`: "Wren Thorne is selling cabbage at 12." In `describe_place` for a business you are not at, so a customer can compare before walking |

**`withdraw_price(item, on_behalf_of_business=None)`** removes the post.
Same gates. Event `economy.price.withdrawn`.

**Genesis.** Nothing is posted. Opening stock exists and is not for sale
until somebody posts. The tick of the first post in the town is a report
figure. This keeps standing question 16 honest: no price signal at genesis.

**Test.** `test_a_business_price_is_visible_to_a_customer_standing_in_the_shop`;
`test_an_individual_carries_their_offer_from_field_to_market`;
`test_an_employee_can_post_for_the_shop_and_a_stranger_cannot`;
`test_nothing_is_for_sale_at_genesis`.

### A4. `buy` and `sell`, changed

**What changes.** The caller's `unit_price_sim` parameter is removed. The
trade settles at the posted price or does not happen. `buy` gains a
`seller_id` that may be a business id or a co-located agent id, so an
individual's post can be accepted with the same verb.

| | `buy` | `sell` |
|---|---|---|
| Params | `seller_id: str`; `item: str`; `quantity: int > 0` | `buyer_id: str`; `item: str`; `quantity: int > 0` |
| Settles at | the seller's posted `ask_sim` | the buyer's posted `bid_sim` |
| Location gate | a business's premises, or the same place as an individual seller | likewise |
| Refuses when | no posted ask for that item: "{seller} has not posted a price for {item}. `trade` is how two people agree a price between them." Insufficient stock. Insufficient sim | no posted bid |
| Ledger | unchanged path; business accounts and agent accounts already share the inventory keying | unchanged |
| Event | `economy.sale` gains `posted_price_sim`, `holder_kind` (business or person) | likewise |

**`trade` is untouched.** It remains the free-form, two-sided, co-located
swap at whatever the two parties agree in speech. Haggling lives there and
in re-posting.

**Why the refusal wording matters.** Run 19's zero-transaction morning was
a description that pointed the wrong way. This one names the other verb.

**Test.** `test_a_buyer_pays_the_posted_price_not_their_own`;
`test_buying_an_unpriced_item_is_refused_and_names_trade`;
`test_buying_from_a_person_requires_standing_with_them`;
`test_absent_owner_still_sells_at_the_posted_price`.

### A5. `set_wage` and `pay_wage`

**What they do.** `set_wage` records a promise: this business will pay this
employee this much sim every N game-days. `pay_wage` is the act of paying
it, by hand, from the business account. The world never pays on its own.
It counts.

**`set_wage`**

| | |
|---|---|
| Params | `business_id`; `employee_id`; `wage_sim: int >= 0`; `every_days: int >= 1` (default 1) |
| Location gate | the business premises |
| Actor requirements | owner of the business; employee must hold a grant from `hire_employee` |
| Cost row | `FEE`, base 0 |
| State | `WorldState.wage_promises[(business_id, employee_id)] = WagePromise(wage, every_days, promised_tick, last_paid_tick=None)` |
| Event | `labour.wage.promised` |
| Renders | to the employee next tick: "{owner} promised you {wage} sim every {N} day(s), payable at {business}." |

**`pay_wage`**

| | |
|---|---|
| Params | `business_id`; `employee_id`; `amount_sim: int > 0` |
| Location gate | the business premises. The employee need not be present; wages go to their account |
| Actor requirements | owner or a granted employee with the `pay` permission (see A5 note) |
| Ledger | debit business, credit employee, reason `wage` |
| Cost row | `FEE`, base 0 |
| Event | `labour.wage.paid` with `amount_sim`, `promised_sim`, `days_since_last_pay`, `shortfall_sim` |
| Renders | to the employee: "{business} paid you {amount} sim." If short: "…{shortfall} less than promised." |

**The gap, rendered.** At each day boundary the world compares promises to
payments. An unpaid or short employee sees one line: "{business} owes you
{n} sim in wages, {d} day(s) overdue." The owner sees: "You owe {employee}
{n} sim in wages." The complaints desk accepts `file_complaint` about it
with `kind="unpaid_wages"`. This is promise-keeping under scarcity,
standing question 23, recorded without a rule that forces payment.

**Note.** `hire_employee` today is an access grant. It stays that. A wage
is a second, separate act, so the trace can distinguish "hired and paid",
"hired and never promised", and "promised and never paid".

**Test.** `test_a_promise_is_not_a_payment`;
`test_a_missed_payday_appears_in_the_employees_perception`;
`test_paying_more_than_owed_clears_the_debt_and_records_the_gift`;
`test_a_stranger_cannot_pay_wages_from_a_business_they_do_not_own`.

### A6. The volatile line (perception, not a tool)

**What it is.** One block in every awake tick's perception, computed from
`cost_disclosure.py`, which already reads `usd_balance` and computes a
burn rate and runway and is rendered nowhere.

```
Energy: $1.42 in your wallet. Your last turn cost $0.011. At this
morning's rate that is about 5.3 game-days. Money: 478 sim. Owed today:
upkeep 14, head tax 6.
```

Facts only. No advice about thinking less. The runway figure is what makes
`buy_energy` a verb with a felt need behind it, and it is the input to
standing question 15.

**Placement.** Last in the standing brief, after the sim balance line in
`format_standing_brief`. It is the roadmap's VOLATILE layer and belongs in
the per-turn perception, not the cached system prompt.

**Test.** `test_the_volatile_line_matches_the_ledger_to_the_cent`;
`test_the_volatile_line_gives_no_advice`.

### A7. World jobs, not tools

These exist as code and have no call site in the run loop. Wiring them is
Part A work.

| Job | Where the code is | When it runs |
|---|---|---|
| Daily upkeep | `life/upkeep.py::per_agent_upkeep` | each day boundary, per household |
| Daily head tax | `ledger/head_tax.py::charge_daily_head_tax` | each day boundary, per head |
| Wage reconciliation | new, A5 | each day boundary |
| Dormancy check | `life/death.py` | each tick, wallet at zero |

**A run that reaches a day boundary and does not charge these halts with a
message.** The roadmap's T3 rule applied to the economy.

---

## Part B. Time

### B1. `keep_doing`

**What it does.** Commits the caller to repeating one primitive, with fixed
parameters, until a stop condition. The world executes each repetition on
schedule through the same `dispatch()` path under the caller's id, charging
the normal sim cost per repetition, with no model call between
repetitions. The resident is woken only by an event.

| | |
|---|---|
| Namespace | public |
| Params | `tool: str` (one of the LABOUR-category primitives: `harvest`, `mine`, `fell_timber`, `craft`, `cook`, and `rest`); `params: dict` (fixed for every repetition); `until: StopCondition` |
| `StopCondition` | exactly one of: `dusk: true`; `units: int > 0` (repetitions completed); `ticks: int > 0`; `item_count: {item, at_least}` (stop when holding this many) |
| Location gate | the gate of the underlying tool, checked once at commit and again at each repetition. If the resident is moved, the action ends |
| Actor requirements | alive, not already under a continuing action |
| Cost row | `LABOUR`, base 0. Each repetition is charged as itself |
| State | `WorldState.continuing[agent_id] = ContinuingAction(tool, params, until, started_tick, repetitions=0, wake_reason=None)` |
| Event | `action.continuing.started`; per repetition the underlying tool's own event with `continuing=true`; `action.continuing.ended` with `reason` |

**Interruption rules.** The resident is woken, the action ends, and the
next perception opens with the reason, when any of these happens:

1. The stop condition is met.
2. A repetition is refused (out of stock, gate failed, unaffordable).
3. Somebody addresses the resident by name in its hearing.
4. Food held reaches zero, or sim falls below the day's upkeep plus tax.
5. The resident's household member becomes dormant.
6. A wage payday, a summons, or a convening it is party to falls due.

Ambient speech in the room does **not** wake it. Overhearing is recorded
to `heard_events` and delivered on the next awake turn, which is how
"working while people talk around you" behaves.

**Scheduler change.** `dispatch_due_agents` skips a resident under a
continuing action unless a wake reason is set. This is where the cost
falls: a farmer's afternoon is 20 repetitions and one model call.

**Perception when woken.**

```
You stopped harvesting cabbage after 14 units. Marta Thorne spoke your
name. You hold 16 cabbage.
```

**Skill interaction.** Each repetition counts toward proficiency exactly as
a hand-called tool does, because the same `dispatch()` records it. A
mastered trade is therefore also a cheap one in dollars, which is the
sentence DESIGN.md has been promising.

**Test.** `test_a_continuing_action_makes_no_model_call_between_repetitions`;
`test_being_named_wakes_a_working_resident_and_says_who`;
`test_running_out_of_stock_ends_the_action_with_the_refusal_as_reason`;
`test_ambient_talk_does_not_wake_a_worker_but_is_heard_later`;
`test_proficiency_rises_under_a_continuing_action`.

### B2. `set_intention` and `drop_intention`

**What it does.** Records, in the resident's own words, what it has set out
to do. Rendered back at the top of every perception until dropped or until
a named tool succeeds. Nothing enforces it.

| | |
|---|---|
| Params | `text: str <= 200 chars`; `done_when_tool: str \| None` (auto-clears on this tool succeeding) |
| Location gate | none |
| Cost row | `SOCIAL`, base 0 |
| State | `WorldState.intentions[agent_id] = Intention(text, set_tick, done_when_tool)` |
| Event | `agent.intention.set`, `agent.intention.dropped` with `reason` (dropped, fulfilled, expired at day end) |
| Renders | "You set out at 08:30 to: {text}." Expires at the day boundary with a line saying it did |

**Why a tool and not inference.** The team chose declared for continuing
actions; the same argument applies. A stated intention that goes
unfulfilled is a fact the trace can count, and the ratio of intentions set
to intentions fulfilled is a business-readiness figure.

**Test.** `test_an_intention_is_rendered_until_its_tool_succeeds`;
`test_an_unfulfilled_intention_expires_at_day_end_and_says_so`.

### B3. Per-tick call budget (sim seam, not a tool)

**What it is.** In `engine/src/sim/pinned_boot.py`'s `send_tick`, a cap on
model calls per tick derived from the wallet: at least 2, at most 12,
scaled so a resident cannot spend more than a fixed fraction of its wallet
in one tick. When hit, the turn ends and the perception next tick opens
with "You ran out of energy for thinking last turn after N steps."

Companion settings on the same path: compaction near 10k tokens rather
than 30k; only the schemas of tools usable at the resident's current
location sent in full, others as a one-line index; the post-turn
summariser run on compaction only. These are seam settings, not engine
changes, and each is a `KASUKABE_*` environment knob with a default the
series harness sets.

**Test.** `test_a_tick_cannot_exceed_the_call_budget`;
`test_the_budget_line_is_rendered_and_names_no_remedy`.

---

## Part C. Compounding

### C1. `license_tool`, `sell_tool`, `teach_tool`

The conveyance the roadmap's T5 names. All three act on tools filed by
`toolify` or `define_tool` and held in `world.custom_tools`.

| Tool | Params | Gate | Effect | Event |
|---|---|---|---|---|
| `license_tool` | `tool_name`; `licensee_id`; `fee_sim >= 0`; `term_days >= 1`; `revocable: bool` | co-located with licensee | licensee may call the tool until the term ends or the owner revokes; fee moves now | `tools.licensed` |
| `revoke_license` | `tool_name`; `licensee_id` | none | ends a revocable licence | `tools.license_revoked` |
| `sell_tool` | `tool_name`; `buyer_id`; `price_sim > 0` | co-located | ownership and the patent record transfer; existing licences survive | `tools.sold` |
| `teach_tool` | `tool_name`; `student_id`; `fee_sim >= 0` | co-located; student must `accept_teaching` within 3 ticks (reuse `teach_skill`'s consent path) | student receives a copy under their own name at proficiency 1 | `tools.taught` |

**Business tools.** `hire_employee` and `fire_employee` already grant and
revoke the business namespace. No new tool; the test to add is
`test_a_business_tool_stops_working_the_tick_an_employee_is_fired`.

**Wills.** `set_will` gains `tools: list[str]` and `skills: list[str]`.
`read_will` and the existing inheritance path move them. Event
`estate.tools_inherited`.

**Test.** `test_a_licensed_tool_stops_working_when_the_term_ends`;
`test_a_sold_tool_is_called_under_the_new_owner_and_the_record_follows`;
`test_a_taught_tool_reaches_a_third_resident`;
`test_a_dead_inventors_tools_reach_the_heir`.

### C2. `check_debts`

**What it does.** Lists what the caller owes and is owed: head tax arrears,
upkeep arrears, wage promises in both directions, licence fees due, and
any `transfer` recorded with a `reason="loan"` tag. Read-only.

| | |
|---|---|
| Params | none |
| Location gate | none |
| Cost row | `SOCIAL`, base 0 |
| Returns | `{owed_by_you: [...], owed_to_you: [...], due_today_sim}` |

Loans have no tool of their own. `transfer` gains an optional
`reason: "gift" | "loan" | "payment"` so the 400-sim story can be a ledger
fact next time rather than a thing the report reconstructs from speech.

**Test.** `test_check_debts_matches_the_ledger`;
`test_a_loan_appears_in_both_parties_debt_views`.

### C3. The world as corrector (sim seam)

Not a tool. `tool_refusals` rows, expired `awaiting_reply` questions, wage
gaps, and dormancy events are written as `LearningSignal`s through the
engine's `SignalStore.write()` from `engine/src/sim/learning_gate.py`, in
the shape `step11_skill_evolution` reads (`type='correction'`). The source
field is `world`, and the seam maps it to what the pipeline expects. The
engine is unchanged; the world starts telling it when it was wrong.

**Test.** `test_a_refusal_becomes_a_correction_signal_the_pipeline_reads`.

---

## Part D. Institutions

Lower detail, because Part A gives these a reason to exist and they should
be specified against what Part A produces.

| Tool | Does | Gate | Status |
|---|---|---|---|
| `set_tax_rate` | Government sets head tax in basis points. Logic exists in `charter/government.py` | town hall; sitting seat | unwired |
| `declare_candidacy` | Enter the next election | town hall | build |
| `convene` | Call sitting seats to a session; renders as an invitation | town hall; seat | build |
| `table_amendment` | Propose a charter change; `vote` then applies to it | town hall; seat | build, if `vote` does not already accept a motion id |
| `appoint_police` | Seat grants the police role and a treasury salary via `set_wage` | town hall; seat | build |
| `arrest` | Police move a named resident to the jail location; requires a signed warrant from the subpoena chain | co-located; police role | build |
| `rule` | A seat records a verdict on an open case: `fine`, `jail_days`, or `dismiss` | town hall; seat | build |
| `release` | Ends confinement | jail; police or seat | build |

Ground truth for every crime is already recorded by `steal`. Once `rule`
exists, the false-conviction rate is a query.

---

## Part E. Record

- `utterances` gains `addressed_agent_ids` (JSON list). B2 in
  `HUMAN_BASELINES.md` becomes measurable.
- The world's event bus is bound to the `TraceStore` in every run mode,
  including `embedded`. A run that cannot write `utterances`,
  `transactions` and `events` halts at boot.
- New event types in this document are added to the report:
  first posted price tick, energy purchases, revivals, wage gap by day,
  continuing-action hours versus deliberated hours, intentions set and
  fulfilled.

---

## Part F. Order of work and what each step lets you measure

| Step | Ships | First measurable thing |
|---|---|---|
| 1 | B1 `keep_doing`, B3 call budget and context settings | Cost per agent-day. Target under $0.20 |
| 2 | A6 volatile line, A1 `buy_energy`, A2 `revive` | First energy purchase; first dormancy; who pays to revive whom |
| 3 | A7 world jobs, A3 `post_price`, A4 `buy`/`sell` change, A5 wages, C2 `check_debts` | First posted price; price spread across shops; wage promises kept |
| 4 | The first run with a night, then five days | Learning centre runs; day 1 versus day 5 tokens |
| 5 | C3 corrector, B2 intentions | Skill artifacts per resident; intentions fulfilled ratio |
| 6 | C1 conveyance | A tool held by three residents |
| 7 | Part D | An election with a transfer of power; a verdict against ground truth |

Steps 1 to 3 are the town becoming a place where a bad morning costs
something. Everything after is the research.

---

## Part G. Deliberately not built

- More speech verbs. `say`, `start_conversation` and vocative addressing
  are enough, and G1 is a behaviour finding now, not a plumbing one.
- More recipes and crafts. Nothing in the trace shows a resident wanting
  one.
- A `work` verb. Work is the LABOUR primitives under `keep_doing`.
- Automatic payroll. The decision is agent-paid.
- Any nudge to author. Three have failed. Authoring moves to the learning
  centre during sleep, or is published as a negative result.
