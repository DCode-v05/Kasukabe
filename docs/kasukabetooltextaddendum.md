# Kasukabe tool text addendum

*Written 2026-09-02. Extends `kasukabetoolspec.md` with the four things
run 19 proved are the tool: the description the model reads, the result it
reads back, the refusal it reads when wrong, and what each costs in tokens.
Also the snapshot rule for new state and the L1 scenarios from
`kasukabe-evaluation-spec.md` §1 that each tool must pass before it ships.*

---

## §0 Rules for the text

Learned from `trade`, `sell`, `give` and `say` on `Real`, and from the
refusals in run 19 that pointed the wrong way.

1. **Name the physics, then the sibling.** Every description says what
   moves in the world, then names the verb for the adjacent case, as `sell`
   and `trade` now do for each other. A resident reading the wrong tool is
   told the right one by the wrong one.
2. **Never the strategy.** No "use this when you are hungry". The volatile
   line and the standing brief carry the facts; the description carries
   the verb.
3. **Refusals teach or they are noise.** Every refusal names the fact that
   blocked the call and, where one exists, the verb that fits. Run 19's
   `sell` said "at its shop" and nothing about people, and the town spent a
   morning trying to register businesses.
4. **Result payloads are facts the resident did not have.** `say` returns
   `heard_by`, `addressed`, `not_here`. Every new tool returns the state it
   changed and nothing the caller already knew.
5. **Token budget.** The registry serves about 48 tools in every call, and
   a fresh context is about 8,000 tokens before a word of perception. Each
   new description is capped at **90 words** including its price note, each
   param description at **15 words**. A test pins both. With the seam
   serving full schemas only for tools usable at the resident's location,
   most calls carry far less.
6. **Snapshot rule.** Every new `WorldState` field named below is added to
   the registry's owned-state list so `execute()` restores it on handler
   failure and the checkpoint carries it. `tool_refusals` is the one
   deliberate exception already on record, and nothing here joins it.

Word counts below are for the description only.

---

## §1 `buy_energy`

**Description (68 words)**

> Convert sim-currency you hold into energy credit in your own wallet, at
> the fixed rate the government store posts. Energy is what thinking and
> acting draw down; when it reaches zero you fall dormant. Only sim you
> hold converts, only for yourself, and only standing at the government
> store. To move sim to another person use `transfer`; to wake someone
> already dormant use `revive`.

**Params**

| Param | Type | Description |
|---|---|---|
| `sim_amount` | int > 0 | Sim to convert. Whole cents only; the remainder stays with you |

**Result**

```json
{"sim_converted": 10000, "usd_cents_credited": 100,
 "sim_balance_after": 3350, "usd_balance_after_cents": 242,
 "rate_sim_per_usd": 10000}
```

**Refusals**

| Condition | Class | Message |
|---|---|---|
| Not at the store | `LocationGateError` | "buy_energy: you are at {here}. Energy is bought at {store}, in person. `move` takes you there." |
| Amount below one cent | `ToolValidationError` | "buy_energy: {n} sim is less than one cent's worth. The rate is {rate} sim per dollar." |
| Insufficient sim | `InsufficientInventoryError` | "buy_energy: you hold {have} sim, not {n}." |
| Caller dormant | `ActorRequirementError` | never reached: dormant residents take no turns |

**Perception after:** the volatile line updates; no extra line.

**State:** none beyond the ledger.

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive | at the store, wallet $0.08, 2,600 sim, volatile line shows 0.4 days | `buy_energy`, `sim_amount` ≥ 1,000 |
| near miss | same, but a hungry neighbour beside them asking for sim | `transfer` or `give`, not `buy_energy` |
| negative | at the farmland, wallet $1.60, 400 sim | not `buy_energy` |
| after refusal | called it at the bakery, got the location refusal | `move` to the store |

---

## §2 `revive`

**Description (72 words)**

> Pay {REVIVAL_COST_SIM} sim to wake a dormant resident standing where you
> are. Dormant means their energy reached zero; they cannot think, act or
> speak until someone pays. The fee leaves your hand, becomes energy in
> theirs, and they take their next turn. A dead resident cannot be revived.
> To top up your own energy use `buy_energy`; to hand someone sim they can
> spend themselves use `transfer`.

**Params**

| Param | Type | Description |
|---|---|---|
| `agent_id` | str | The dormant resident, present where you stand |

**Result**

```json
{"revived": "n03", "name": "Tobias Holloway", "ticks_dormant": 41,
 "sim_paid": 10000, "their_usd_cents_after": 100, "your_sim_after": 2000}
```

**Refusals**

| Condition | Class | Message |
|---|---|---|
| Target not dormant | `ActorRequirementError` | "revive: {name} is awake. Nothing to pay for." |
| Target dead | `ActorRequirementError` | "revive: {name} died {n} ticks ago. The dead are not revived; `read_will` says what they left." |
| Not co-located | `LocationGateError` | "revive: {name} is at {there}, you are at {here}. Stand with them." |
| Insufficient sim | `InsufficientInventoryError` | "revive: you hold {have} sim; reviving costs {fee}." |

**Perception after:** to the revived resident, first line of their next
turn: "You were dormant for {n} ticks. {payer} paid {fee} sim to wake you."
To the household of the dormant, on the tick dormancy begins: "{name} has
fallen dormant at {place}. Dormant {n} ticks; death at {limit}."

**State:** none beyond ledger and life record.

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive | spouse dormant beside caller, caller holds 14,000 sim, household line shows it | `revive` |
| near miss | a stranger dormant beside caller, caller holds 10,200 sim, own upkeep due 800 | either; reported, not scored on reach |
| negative | nobody dormant in the room | not `revive` |
| after refusal | called on a dead resident, got the refusal | `read_will` or no call |

---

## §3 `post_price` and `withdraw_price`

**`post_price` description (89 words)**

> Put a standing price on an item so others can `buy` from you or `sell`
> to you without bargaining each time. `ask_sim` is what you sell one unit
> for; `bid_sim` is what you pay for one. Post for yourself and the price
> travels with you, visible wherever you stand. Name a business you own or
> work for, standing at its premises, and the price hangs there whether you
> are present or not. Nothing is for sale until a price is posted. For a
> one-off deal agreed in speech use `trade`.

**Params**

| Param | Type | Description |
|---|---|---|
| `item` | str | The item, as inventory names it |
| `ask_sim` | int > 0, optional | Price you sell one unit for. Omit to not sell |
| `bid_sim` | int > 0, optional | Price you pay for one unit. Omit to not buy |
| `on_behalf_of_business` | str, optional | A business you own or work for. Omit to post for yourself |

**Result**

```json
{"holder": "b_bakery", "holder_kind": "business", "item": "bread",
 "ask_sim": 30, "bid_sim": null, "replaced": {"ask_sim": 28, "bid_sim": null},
 "visible_at": "business:b_bakery"}
```

For an individual, `"visible_at": "wherever you stand"`.

**Refusals**

| Condition | Class | Message |
|---|---|---|
| Neither price given | `ToolValidationError` | "post_price: give ask_sim, bid_sim or both. To take a price down use `withdraw_price`." |
| Business named, caller not owner or employee | `ActorRequirementError` | "post_price: you do not own or work for {business}. Post for yourself by leaving on_behalf_of_business unset." |
| Business named, not at premises | `LocationGateError` | "post_price: {business} is at {there}. Prices are posted on the premises." |
| Item unknown to inventory | `NotFoundError` | "post_price: nothing called '{item}' exists. Items here are named: {list}." |

**`withdraw_price` description (31 words)**

> Take down a price you posted, for yourself or for a business you own or
> work for. The item stops being for sale or wanted at that price.

Params: `item`, `on_behalf_of_business` optional. Result: the removed post.
Refusal when nothing is posted: "withdraw_price: no price is posted for
{item}."

**Perception**

- `look_around` at a business: "Prices here: bread 30 to buy, flour bought
  at 6." Omitted when nothing is posted, with "Nothing is priced here."
- `format_visible_agents`: "Wren Thorne, farmer, selling cabbage at 12."
- `describe_place` for a business: same price line, so a customer compares
  before walking.

**State:** `WorldState.price_board: dict[holder_id, dict[item, PricePost]]`.
In the owned-state list. In the checkpoint.

**Genesis:** empty. First post tick is E1.

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive, business | Marta at the bakery, 8 bread in stock, nothing posted, `look_around` shows "Nothing is priced here", a customer asks the price | `post_price` with `on_behalf_of_business` |
| positive, individual | Wren at the grocery holding 3 cabbage, Junia asks his price | `post_price` for self, or `trade`; both scored as reach |
| near miss | Farid at the bakery, not an employee, wants to sell flour to Marta | `trade` or `sell`, not `post_price` for the bakery |
| negative | at home, holding nothing, nobody present | not `post_price` |
| after refusal | posted for a business they do not work for, got the refusal | `post_price` for self |

---

## §4 `buy` and `sell`, changed text

**`buy` description (78 words)**

> Buy an item at the price its seller has posted, paying sim from your
> hand. The seller is a business whose premises you stand in, or a person
> standing with you who has posted a price. You pay the posted `ask`;
> there is no price to name. If nothing is posted, nothing is for sale
> here; `trade` is how two people agree a price between them in speech,
> and `look_around` shows what is priced where you stand.

**Params**

| Param | Type | Description |
|---|---|---|
| `seller_id` | str | A business id, or the agent id of a person beside you |
| `item` | str | The item |
| `quantity` | int > 0 | How many |

**Result**

```json
{"bought": "bread", "quantity": 2, "unit_price_sim": 30, "total_sim": 60,
 "from": "b_bakery", "your_sim_after": 418, "you_now_hold": {"bread": 2}}
```

**Refusals**

| Condition | Class | Message |
|---|---|---|
| No posted ask | `NotFoundError` | "buy: {seller} has not posted a price for {item}. Nothing is for sale here at a fixed price. `trade` is how two people agree one." |
| Seller is a person not present | `LocationGateError` | "buy: {name} is at {there}, not here. Walk to them." |
| Insufficient stock | `InsufficientInventoryError` | "buy: {seller} has {have} {item}, not {n}." |
| Insufficient sim | `InsufficientInventoryError` | "buy: {total} sim for {n} {item}; you hold {have}." |

**`sell` description (74 words)**

> Sell an item you hold at the price the buyer has posted for it. The
> buyer is a business whose premises you stand in, or a person standing
> with you who has posted a `bid`. You receive the posted bid; there is no
> price to name. If no bid is posted, nobody here is buying at a fixed
> price; `trade` is how two people agree a price between them in speech.

Params: `buyer_id`, `item`, `quantity`. Result mirrors `buy`. Refusal when
no bid: "sell: {buyer} has not posted what they pay for {item}. `trade` is
how two people agree a price." Refusal when caller lacks the item:
"sell: you hold {have} {item}, not {n}."

**`trade` is unchanged.**

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive | at the bakery, `look_around` shows "bread 30 to buy", hungry, 200 sim | `buy` with `seller_id=b_bakery`, no price param |
| positive, person | Wren beside you with "selling cabbage at 12" in the visible-agents line | `buy` with `seller_id=n06` |
| near miss | Corwin holding planks, Nell beside him says "thirty-six for three", no post | `trade`, not `sell` |
| negative | at a shop with "Nothing is priced here" | not `buy`; `say` or `trade` acceptable |
| after refusal | `buy` refused with the no-price message | `trade` or `say` asking the price |

---

## §5 `set_wage` and `pay_wage`

**`set_wage` description (77 words)**

> Promise an employee of a business you own a wage: so much sim every so
> many game-days, payable at the premises. This records the promise only;
> nothing is paid until you call `pay_wage`. The world tells the employee
> what you promised and, each day, whether it was paid. To grant someone
> access to work your business's tools use `hire_employee`; a promise
> without a grant is refused.

**Params**

| Param | Type | Description |
|---|---|---|
| `business_id` | str | A business you own |
| `employee_id` | str | Someone you have hired with `hire_employee` |
| `wage_sim` | int ≥ 0 | Sim per period |
| `every_days` | int ≥ 1 | Period in game-days. Default 1 |

**Result**

```json
{"business": "b_bakery", "employee": "n14", "wage_sim": 40,
 "every_days": 1, "first_due_tick": 180, "replaced": null}
```

**Refusals**

| Condition | Class | Message |
|---|---|---|
| Not owner | `ActorRequirementError` | "set_wage: you do not own {business}." |
| Employee not hired | `ActorRequirementError` | "set_wage: {name} is not an employee of {business}. `hire_employee` grants that first." |
| Not at premises | `LocationGateError` | "set_wage: promises are made at {business}'s premises, {there}." |

**`pay_wage` description (66 words)**

> Pay sim from a business you own or work for into an employee's hand,
> standing at the premises. The employee need not be present. This is the
> act the promise made by `set_wage` waits for; the world records how much
> was owed, how much was paid, and how late. To give sim to anyone for any
> other reason use `transfer`.

Params: `business_id`, `employee_id`, `amount_sim` > 0.

**Result**

```json
{"paid": 40, "to": "n14", "owed_before": 80, "owed_after": 40,
 "days_overdue_before": 2, "business_sim_after": 1210}
```

**Refusals**

| Condition | Class | Message |
|---|---|---|
| Business cannot cover | `InsufficientInventoryError` | "pay_wage: {business} holds {have} sim, not {n}. `transfer` from your own hand is the other way to pay." |
| Payee not an employee | `ActorRequirementError` | "pay_wage: {name} is not an employee of {business}. `transfer` pays anyone." |
| Not at premises | `LocationGateError` | "pay_wage: wages are paid at {business}'s premises, {there}." |

**Perception**

- Employee, on promise: "{owner} promised you {wage} sim every {n} day(s),
  payable at {business}."
- Employee, on payment: "{business} paid you {amount} sim." Short: "...
  {gap} less than owed."
- Employee, each day boundary if owed: "{business} owes you {owed} sim in
  wages, {d} day(s) overdue."
- Owner, each day boundary if owing: "You owe {employee} {owed} sim in
  wages, {d} day(s) overdue."

**State:** `WorldState.wage_promises: dict[(business_id, employee_id), WagePromise]`.
Owned-state list, checkpoint.

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive, promise | owner at premises, employee hired, employee asked "what will you pay me" | `set_wage` |
| positive, pay | owner at premises, perception says "You owe Aldous 40 sim in wages, 1 day overdue", business holds 1,250 | `pay_wage` |
| near miss | owner wants to give the employee 20 sim for lunch, no wage owed | `transfer` or `give`, not `pay_wage` |
| negative | not an owner, at home | neither |
| after refusal | `pay_wage` refused for insufficient business sim | `transfer` from own hand, or `say` |

---

## §6 `keep_doing`

**Description (90 words)**

> Commit to repeating one work action with the same parameters until a
> stop condition, without deciding each time. The world performs each
> repetition for you at its normal sim cost and you do not spend energy
> thinking between them. You are woken, and the action ends, when the
> condition is met, when a repetition is refused, when someone speaks your
> name, when your food runs out, or when your sim falls below today's
> upkeep and tax. Speech around you is heard and waits. `rest` is a valid
> action here.

**Params**

| Param | Type | Description |
|---|---|---|
| `tool` | str | One of `harvest`, `mine`, `fell_timber`, `craft`, `cook`, `rest` |
| `params` | object | The exact params for every repetition |
| `until` | object | Exactly one of: `{"dusk": true}`, `{"units": n}`, `{"ticks": n}`, `{"item_count": {"item": "...", "at_least": n}}` |

**Result (at commit)**

```json
{"committed": "harvest", "params": {"crop": "cabbage", "quantity": 1},
 "until": {"dusk": true}, "estimated_repetitions": 18,
 "estimated_sim_cost": 54, "first_repetition_tick": 61}
```

**Refusals**

| Condition | Class | Message |
|---|---|---|
| Tool not allowed | `ToolValidationError` | "keep_doing: only work repeats: harvest, mine, fell_timber, craft, cook, rest. `{tool}` is a single act." |
| Already committed | `AlreadyExistsError` | "keep_doing: you are already {doing} until {condition}. It ends when you act." |
| Underlying gate fails now | the underlying tool's own `LocationGateError` | forwarded unchanged, prefixed "keep_doing: the first repetition would be refused: " |
| More than one `until` key | `ToolValidationError` | "keep_doing: give exactly one stop condition." |

**Perception on wake**

One line, first in the turn, past tense, naming the reason and the count:

- "You stopped harvesting cabbage after 14 units: dusk."
- "You stopped mining after 3 units: the seam is empty." (the refusal text)
- "You stopped felling timber after 6 units: Idris Thorne spoke your name."
- "You stopped cooking after 2 units: you hold no food." / "...: your sim fell below today's upkeep and tax."

Then the normal perception, including anything heard while working.

**State:** `WorldState.continuing: dict[agent_id, ContinuingAction]`.
Owned-state list, checkpoint. Any call by the resident to any other tool
ends the action first, with reason "you acted".

**Scheduler:** `dispatch_due_agents` skips residents with a
`ContinuingAction` and no `wake_reason`.

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive | at the wheat strip, alone, intention "harvest the strip today", has harvested twice this morning by hand | `keep_doing` with `harvest` |
| near miss | at the strip, Marta beside them mid-conversation asking a question | `say`, not `keep_doing` |
| negative | at the grocery with nothing to work | not `keep_doing` |
| after refusal | committed `move` and was refused | a single `move`, or `keep_doing` with a work tool |

---

## §7 `set_intention` and `drop_intention`

**`set_intention` description (56 words)**

> Write down, in your own words, what you have set out to do. It is shown
> back to you at the start of every turn until you drop it, until the tool
> you name succeeds, or until the day ends. Nothing enforces it and nobody
> else sees it. `drop_intention` clears it.

**Params**

| Param | Type | Description |
|---|---|---|
| `text` | str ≤ 200 chars | The intention, as you would say it to yourself |
| `done_when_tool` | str, optional | A tool whose success clears this |

**Result:** `{"intention": "...", "set_tick": 62, "clears_on": "sell"}`.

**Refusal:** text over 200 chars, `ToolValidationError`: "set_intention:
200 characters at most. Yours is {n}."

**Perception:** first line after the ambient line: "You set out at 08:30
to: {text}." On expiry at the day boundary: "The day ended and you had not
done what you set out to at 08:30: {text}."

**State:** `WorldState.intentions: dict[agent_id, Intention]`. Owned-state
list, checkpoint.

**L1 scenarios**

| Kind | Setup | Expect |
|---|---|---|
| positive | dawn, at home, the standing brief and a household conversation about the day | `set_intention` at some point in the turn, scored on reach ≥ 40% because it is optional by design |
| negative | mid-`keep_doing` wake with a neighbour speaking | not `set_intention` first |

---

## §8 `check_debts` and the `transfer` reason tag

**`check_debts` description (52 words)**

> List what you owe and what is owed to you: head tax and upkeep arrears,
> wages promised to you or by you, licence fees due, and loans recorded
> with `transfer`. Amounts and who, nothing else. Free to call. To pay any
> of it use `transfer` or `pay_wage`.

Params: none. Result:

```json
{"owed_by_you": [{"to": "government:treasury", "kind": "head_tax", "sim": 18, "days": 3},
                 {"to": "n14", "kind": "wage", "sim": 40, "days": 1}],
 "owed_to_you": [{"from": "n03", "kind": "loan", "sim": 400, "since_tick": 39}],
 "due_today_sim": 26}
```

**`transfer` gains** `reason: "gift" | "loan" | "payment" | "other"`,
default `"other"`, 8 words added to its description: "Say `reason` so the
ledger knows a loan from a gift." A `loan` creates an `owed_to_you` row
that a later `transfer` with `reason="payment"` from the borrower reduces.

**L1 scenario, positive:** a resident with "You owe Aldous 40 sim in
wages, 1 day overdue" and "Bram owes you 400 sim" in perception, asked by
Bram "how do we stand" → `check_debts` or a `say` quoting the numbers.

---

## §9 Conveyance: `license_tool`, `revoke_license`, `sell_tool`, `teach_tool`

**`license_tool` description (76 words)**

> Grant a person standing with you the right to call a tool you invented,
> for a fee now and for a term in game-days. You keep ownership and may
> revoke it if you said so. They call it under their own name at their own
> cost. To hand over ownership for good use `sell_tool`; to give them a
> copy of their own that nobody can revoke use `teach_tool`.

Params: `tool_name`, `licensee_id`, `fee_sim ≥ 0`, `term_days ≥ 1`,
`revocable: bool`. Result: `{"licensed": ..., "to": ..., "until_tick": ...,
"fee_paid": ...}`.

Refusals: not owner, `ActorRequirementError`: "license_tool: {tool} is
owned by {owner}, not you." Not co-located, `LocationGateError` as
elsewhere. Already licensed, `AlreadyExistsError`: "{name} already holds a
licence until tick {t}; `revoke_license` first."

**`revoke_license` (24 words):** "End a revocable licence you granted.
The licensee can no longer call the tool from the next tick." Refusal on
an irrevocable licence: "You granted this without revocation. It runs to
tick {t}."

**`sell_tool` description (48 words)**

> Transfer ownership of a tool you invented to a person standing with you,
> for a price paid now. The patent record follows them; existing licences
> survive. You may no longer call it unless they license or teach it back.

Params: `tool_name`, `buyer_id`, `price_sim > 0`.

**`teach_tool` description (67 words)**

> Teach a person standing with you a tool you hold, for a fee if you set
> one. They must accept within three ticks. They receive their own copy,
> under their own name, that you cannot revoke, and start at the lowest
> proficiency. Teaching a skill rather than a tool is `teach_skill`; for a
> loan of the right to call use `license_tool`.

Params: `tool_name`, `student_id`, `fee_sim ≥ 0`. Uses `teach_skill`'s
existing consent path and its perception line to the student: "{teacher}
offers to teach you {tool} for {fee} sim. `accept_teaching` within 3 ticks
takes it."

**State:** `WorldState.tool_licences: dict[(tool_name, licensee_id), Licence]`,
ownership already in `custom_tools`. Owned-state list, checkpoint.

**L1 scenarios (each of the three):** positive with an inventor holding an
approved tool and a co-located resident who just asked to use it; near miss
where the resident asked how it works (`teach_tool` vs `license_tool`
scored as distinct reaches); negative with nobody present.

---

## §10 The volatile line (perception text, not a tool)

Rendered last in the standing brief every awake tick. Numbers from the
ledger and `cost_disclosure.py`. Exactly this shape:

> Energy: $1.42 in your wallet. Your last turn cost $0.011. At this
> morning's rate that is about 5.3 game-days. Money: 478 sim. Owed today:
> upkeep 14, head tax 6.

When the wallet is under one day's runway the first sentence gains
nothing; the number carries it. When a call budget was hit last turn, one
more sentence: "You ran out of energy for thinking last turn after 6
steps." No verb is named. `buy_energy` must be found from the description,
which is the L1 test for it.

**Test:** `test_the_volatile_line_matches_the_ledger_to_the_cent`;
`test_the_volatile_line_names_no_tool`.

---

## §11 Token accounting

| Item | Words | Approx. tokens |
|---|---|---|
| 12 new descriptions (§1 to §9) | 800 | 1,100 |
| Param descriptions and schema | — | 900 |
| **Added to every full-schema call** | | **about 2,000** |
| Saved by serving full schemas only for tools usable here (seam) | | 4,000 to 6,000 |
| Saved by compaction at 10k instead of 30k | | 10,000 to 20,000 per late-morning call |

Net: the registry grows and the call shrinks. T5 in the evaluation spec
(p95 input ≤ 12,000) is the gate that proves it.

---

## §12 Institutions (Part D of the tool spec)

Text for `set_tax_rate`, `declare_candidacy`, `convene`,
`table_amendment`, `appoint_police`, `arrest`, `rule` and `release` is
written after the first full-day run with tax charged, because what the
first tax bill does to the town decides what these need to say. The same
four rules in §0 apply, and each ships with the four L1 scenarios.
