# Spec: an economy that can thrive, a market that clears, and politics that change the mechanics

*Cross-cutting specification drawn from `12-economy-and-life.md` (ECON), `13-governance-organisations-justice.md` (GOV), `11-world-tools-and-physics.md` (TOOL), `14-world-core-runner-and-record.md` (CORE) and `18-growth-construction-expansion.md` (GROW). Those carry the file:line evidence; this is the build plan.*

## 1. The target, stated so it can be failed

- A household that works clears its daily bill with a surplus, and the
  surplus can buy a meaningful amount of energy (thinking time).
- A business functions as a business: stock on a shelf, a price on the door,
  a till its owner can draw from, staff on a wage that is actually paid.
- Prices, wages and rents move with supply and demand: a shortage raises the
  ask, a glut lowers it, a shop that never restocks loses its customers.
- The government has revenue, a budget, and spending that reaches residents
  (salaries, pensions, public works, relief), so a tax rate is worth arguing
  about.
- An ideology change, by election or amendment, changes the mechanics on the
  next tool call: price caps, wage floors, rationing, collective ownership,
  property rules, licensing, welfare, borders.

Acceptance, in a mock run that crosses at least five day boundaries:

1. Net worth (sim plus stock at posted prices) of at least half the
   households rises from day 1 to day 5.
2. Every day boundary charges upkeep and a non-zero head tax, records the
   shortfall as a debt, and pays salaries and pensions from the treasury.
3. At least one resident buys energy with sim earned in the run.
4. A posted bread price rises after a day of unmet demand and falls after a
   restock, with the control grocer as the reference.
5. A `price_caps` policy set by vote makes the next over-cap `post_price`
   refuse, with the rule quoted and a correction signal written.
6. A debt seeded from persona text is visible to both parties through
   `check_debts`, is settled by one transfer, and a second payment is refused.

## 2. Where it stands today

| Fact | Finding |
|---|---|
| Head tax is charged at 0 bps; the "day was charged" gate counts calls, not sim | ECON-01, GOV-06 |
| Upkeep is charged at the first tick of every run, invisibly, as a `TRANSFER` | ECON-02, ECON-03, CORE-04 |
| Every sim sink lands in the treasury and nothing spends the treasury: no payroll, pensions, tenders, relief | ECON-04, GOV-08 |
| At 10,000 sim per dollar and ~$2 per agent-day, a day of thinking costs ~20,000 sim against a 90-sim bill; energy is unbuyable from work | ECON-05 |
| A business owner cannot stock the shelf or draw from the till; the only route is to hire and pay yourself | TOOL-03, TOOL-15 |
| Residents cannot see what they hold | TOOL-04, CORE-16 |
| Genesis debts never reach the ledger; head-tax debt cannot be repaid; upkeep shortfall is never a debt; one bite resets hunger | ECON-13/14/15/16 |
| Illness and hunger have no effect on work; treatment has no tool | ECON-06, ECON-12 |
| Money is not inherited; births are unreachable; population dials ignored | ECON-07, ECON-23, ECON-11 |
| The market is a board of standing asks with no quantity, no stock view, no spoilage, no demand; the supply/demand rulebook is read only by the browser | TOOL-21, ECON §5.5 |
| The only policy lever that changes a mechanic is the tax rate; margin, pensions, permits are dead levers; no amendment can pass | GOV-03, GOV-09, GOV §5.4 |

## 3. Principles

- Two currencies, one law: USD is energy (metered per token), sim is money
  (everything else). Actions cost sim as built; fix the persona text that
  says otherwise (ECON-24, operator question 1 in doc 12).
- Money must have a source as well as sinks. Decide explicitly between a
  fixed supply with a redistributing treasury and a mint tied to production
  (ECON-04). Recommended: fixed supply plus a treasury that spends, with the
  control businesses as price references, and revisit once prices are
  observed.
- Every obligation is one record with one settle function; every policy is
  one field read at dispatch; every change is one event.

## 4. The mechanism

### 4.1 Day jobs, once, for both hosts

Extract the day-boundary work from `GenesisRunner` into `world/life/day_jobs.py`
called from both composition roots (ECON-08, CORE-04): upkeep, head tax,
wage accrual and reconciliation, pensions, government payroll, tender
payouts, relief, obligation interest and overdue events, intention expiry,
inheritance on death. Charge on `is_day_boundary` (the day just completed)
and say so in the volatile line. Initialise the day counter to the boot day.
Emit typed events for each with a bus sink, so the trace and the report see
them (CORE-03).

Transaction reasons: add `UPKEEP`, `ACTION_COST`, `REFUND`, `FEE`,
`DONATION`, `REVIVAL`, `ENERGY_EXCHANGE`, `WAGE`, `PENSION`, `SALARY`,
`FINE`, `RELIEF`, `TENDER` so a bill and a gift are distinguishable (ECON-03,
GOV-15).

### 4.2 The obligation ledger

`WorldState.obligations`, owned state, checkpointed. One record:

```
Obligation{id, kind: loan|wage|rent|tax|upkeep|fine|tuition|tender|contract,
           creditor, debtor, principal, outstanding, due_tick, period_ticks,
           interest_bps, penalty_bps_per_day, status, origin, memo}
```

Writers: genesis persona debts (`NeighbourOpinion(kind="debt")`, ECON-13),
`transfer(reason="loan")`, `set_wage` (periodic), head-tax and upkeep
shortfalls, `rule` fines, `award_tender`, `offer_contract`. Settlers:
`transfer(reason="payment", obligation_id)`, `pay_wage`, a new `pay_tax`,
milestone payments. One `settle(obligation_id, amount)` function. Day job:
accrue periodic obligations, apply interest, emit `economy.obligation.overdue`
with `involved_agent_ids` (so the debtor and creditor are woken), auto-file
to the complaints desk after N days. `check_debts` reads it; the volatile
line shows what is due today. `loans`, `wage_promises`, `arrears` and
`HeadTaxDebtStore` become adapters over it.

### 4.3 The treasury spends

Revenue exists (tax, fees, fines, donations, energy purchases). Add
spending in the day job (GOV-08, ECON-09, ECON-10): salaries for every
filled `Job` and appointed police; pensions at the policy percentages for
residents past the eligibility age; relief to residents below a threshold;
tender escrow and milestone payments (doc 05); a `reserve_floor_sim` policy
below which discretionary spending stops. Seed the treasury with roughly ten
days of payroll and pensions at genesis. Tools: `create_job`, `cut_job`,
`post_tender`, `award_tender`, `set_policy` (doc 07).

### 4.4 Re-rate the two currencies together

Pick numbers so that one working day's surplus buys about one game-day of
cognition at the budgeted burn (ECON-05, doc 12 §7). Proposal:
`SIM_PER_USD = 2,000`, burn at or below $0.15 per agent-day (needs the
pacing and context work in doc 14), upkeep 90 per head, head tax 300 bps,
starting sim 2,000 per adult, business float about three days of payroll and
restock, wallets sized to two game-days of burn and to at most 60 percent of
the run's cap so dormancy is reachable. Re-derive revival, blessing, wedding,
registration and the schedule table in the same commit. If the pinned
10,000:1 is kept, scale every sim figure by five; the ratios are the point.

### 4.5 Businesses as businesses

- Shelf and till primitives (TOOL-03): `stock_shelf`, `take_from_shelf`,
  `deposit_to_till`, `draw_from_till`, owner or staff at the premises, with
  events. Self-hire refused; hiring requires consent or co-location
  (TOOL-15).
- Premises are real places (TOOL-01, TOOL-11): a business's `location_id`
  is a canonical map place set at genesis or by a `premises` parameter on
  `register_business`, and a business provides the workshops its trade
  needs (mill gives `mill`, bakery gives `kitchen`) so the miller can mill
  in her own mill (TOOL-02).
- Wage jobs produce: an employee under `keep_doing("work", business)` adds
  the business's goods to its shelf each repetition, so "does its job" is a
  mechanism (TOOL §5).
- Books: `/businesses/pnl` already exists; write `business.*` events so it
  reads real rows, and surface profit and loss in the viewer (doc 16).

### 4.6 A market that clears

In order (ECON §5.5, TOOL-W14):

1. Residents see what they hold, what the shop holds, and the prices here,
   every tick (TOOL-04, CORE-16). Quantity on every posted price; stock
   beside the price in `look_around`.
2. Demand: the food component of upkeep is consumed in kind. Eating from
   inventory satisfies it; otherwise the household buys or goes hungry.
   Shelter and heat stay cash and accrue as obligations when unpaid
   (ECON-16). Perishables spoil per day (per-lot age in `Inventory`).
3. Reference prices: the control businesses act as scripted price-takers
   driven by the elasticity, buffer and spoilage parameters in
   `world/rules/village_rules.json`, which today only the browser reads
   (TOOL-21). Residents undercut or overprice them; price discovery is
   measured against that floor and ceiling.
4. A clearing venue: `market_order(item, side, quantity, limit)` at the
   marketplace with a per-tick uniform-price match and an
   `economy.market.cleared` event carrying price and volume. Posted boards
   stay for shops. A `market_report` read tool returns median ask, stock and
   the last settlements.
5. Capacity: illness and hunger scale LABOUR output at dispatch through
   `effective_work_capacity` (ECON-06); mastery raises it. `treat_illness`
   at the pharmacy, fee to the pharmacy's account (ECON-12, ECON-29).

### 4.7 The policy engine: ideology becomes mechanics

`PolicyBook` in `charter/policy.py`, extending `GovernmentPolicy`, frozen and
versioned like it, every field defaulting to today's free market (GOV §5.4).
Fields and the code that reads them:

| Field | Read by |
|---|---|
| `tax_rate_bps` | day job (exists) |
| `income_tax_bps` | `pay_wage`, `buy`, `sell`, `trade` settlement |
| `credit_store_margin_bps` | `buy_energy` via `exchange_sim_for_usd` |
| `price_caps`, `price_floors` per item | `post_price`, `buy`, `sell`, `trade`, `market_order` |
| `wage_floor_sim` | `set_wage`, wage reconciliation |
| `transfer_ceiling_sim`, `transfer_floor_sim` | `transfer`, `give` |
| `rationing` per item per head per day | `buy`, `market_order`, reset at the day boundary |
| `property_transfer_rule`: free, licensed, forbidden, collective | `transfer_tile`, `auction_land`, `auction_plot`, `transfer_business` |
| `collective_ownership` by kind | `buy`, `sell` proceeds; `harvest` yield to a common storage |
| `business_licensing`: open, permit required, state only | `register_business` |
| `zoning` per sector | `open_site` (doc 05) |
| `relief_payment_sim`, `relief_threshold_sim` | day job |
| `pension_*`, `salary_schedule`, `reserve_floor_sim` | day job |
| `closed_borders`, `emergency_powers` | birth grant, movement into expansion sectors, reserve bypass |

Plumbing: `world.policy()` cached per tick; one registry hook
`apply_policy_gates(spec, world, actor, params)` between the location gate
and the handler so every policy refusal is a uniform `PolicyRefusal` quoting
the rule and writing a correction signal; money effects live in the
handlers and the day job. Routine levers change through a seat tool
`set_policy(field, value)`; structural ones (property, collective
ownership, licensing, borders, election rules) through the three-fifths
amendment path once `table_amendment` and `vote_amendment` exist (GOV-03).
Every change emits `politics.policy.set` with old, new, who, version and
proposal id, and the governance viewer's diff extends to policies.

Citizens' ideology (GOV §5.4): six research axes (redistribution, state
control, property, openness, authority, piety), never shown as labels;
measured behaviourally from transactions and votes, stated from the engine's
evolved soul via the seam, and probed by a dream-probe battery on the
background lane during sleep. Candidacies carry a platform over policy
fields; a winning platform is auto-tabled so a mandate has a mechanical
consequence. Drift from the genesis persona on these axes is ROADMAP's
compounding quantity 4 made measurable.

## 5. Decisions the operator has to make

1. Fixed sim supply with a spending treasury, or a mint tied to production.
2. `SIM_PER_USD` at 2,000 with a burn target, or keep 10,000 and scale the
   sim price level by five.
3. Actions priced in sim as built (recommended) or in USD as the design text
   says; fix the persona text either way.
4. Genesis tax rate: 300 bps from `village_rules.json`, or zero until the
   first government sets one (then nothing is ever taxed until elections
   run, doc 07).
5. Upkeep at midnight (recommended) or at dawn; food payable in kind
   (recommended).
6. Head-tax payer: the household's richest earner (recommended) rather than
   the first member.
7. Do the temple, pharmacy and college get accounts, so fees are income for
   a keeper, a pharmacist and an instructor.
8. Is a third of the cast reaching $0 in a run the intended stakes
   calibration, or should first full-day runs keep dormancy rare.

## 6. Work items, in order

| # | Item | Findings | Effort | Depends on |
|---|---|---|---|---|
| E1 | Real head-tax rate; S1 checks sim not counts; day jobs at real boundaries; typed transaction reasons | ECON-01/02/03, GOV-06, CORE-03/04 | S | none |
| E2 | `day_jobs.py` for both hosts with treasury outflows: payroll, pensions, relief, tenders | ECON-04/08/09/10, GOV-08 | M | E1 |
| E3 | Re-rate the currencies and size wallets, business float and treasury | ECON-05/32 | S code, L cost work | doc 14 pacing |
| E4 | Obligation ledger; genesis debts seeded; `pay_tax`; overdue to the desk | ECON-13/14/15, TOOL-18 | M | E1 |
| E5 | Shelf and till; premises are real places; workshops belong to businesses; holdings in perception | TOOL-01/02/03/04/11/15 | M | none |
| E6 | Food in kind, capacity at dispatch, treatment tool, inheritance of money, dormancy threshold, births, dials | ECON-06/07/11/12/16/21/23 | M | E2 |
| E7 | Honest prices in front of the resident: prefix-normalised `quote_action_cost`, distance-priced `move`, upkeep and tax in the volatile line, three numeric law rules | ECON-17/18/20/24 | S | none |
| E8 | Market substrate: quantity on posts, stock in view, spoilage, control businesses as price-takers, clearing venue, `market_report` | TOOL-21, ECON §5.5 | L | E5, E6 |
| E9 | `PolicyBook` with the four cheapest levers first (price caps, wage floor, income tax, relief), then property, collective, licensing, borders | GOV §5.4, GOV-09, ECON-30 | M | doc 07 amendment tools |
| E10 | Control bakery and `stakes_report` wired; behavioural tests replace source-string tests | ECON-25/26 | S | E1 |
| E11 | Ideology measurement: beliefs rows, platforms on candidacy, dream-probe battery | GOV §5.4, doc 04 | M | doc 08 |
