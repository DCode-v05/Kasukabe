*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`ECON-nn`) are referenced from the cross-cutting documents.*

# Review C: economy and life (prefix ECON)

Read-only review of `app/world/ledger/**`, `app/world/life/**`, `app/world/control_bakery.py`, `app/world/stakes_report.py`, the economy/life parts of `app/world/run_genesis.py` and `app/world/wiring.py`, and the broker's wallet path (`app/broker/ledger.py`, `app/broker/spend.py`), against `Real` at 5400e94 (2026-09-03). Every claim is cited as `path:line` (paths relative to `/home/user/kasukabe/app` unless they start with `docs/` or are a root `.md`). Where I infer rather than read, the sentence says "inferred".

Test run requested by the brief: `cd app && uv run pytest world/ledger world/life -q -x` gives **307 passed in 2.35s** (deps installed by `uv` on first run).

---

## 0. Ten-line summary

1. The dual-currency ledger is sound and the $1000 invariant is structurally enforced (`world/ledger/core.py:311-426`). Nothing in scope can mint or lose a dollar.
2. Upkeep, head tax, wage accrual, the zero-wallet dormancy sweep and the 24-hour death clock ARE now called from the live `run_genesis` loop (`world/run_genesis.py:4565-4686`). They are NOT called from the `world/main.py`+`wiring.py` root, which the roadmap says should be the only server (ECON-19).
3. Upkeep was in fact charged in run 23, at the first tick of day 0, not "never": Bram 3200 to 2750 and Wren 3800 to 3350 are exactly one 450-sim household bill each. The run report could not see it because upkeep is written as `TransactionReason.TRANSFER` (ECON-02, ECON-03).
4. Head tax is charged at the genesis rate of 0 bps, so it bills 0 sim and the S1 "day was charged" gate passes vacuously (ECON-01).
5. Sim-currency is a one-way drain: every sink lands in the treasury and nothing spends the treasury. Thriving is impossible by construction until the town has a sim source (payroll, pensions, tenders, public works) (ECON-04).
6. At 10,000 sim per dollar and a measured $2 per agent-day, a day's cognition costs ~20,000 sim against a ~90-sim upkeep bill and a 15-30-sim loaf. No honest day's work can ever buy energy. Either the exchange rate or the cost per agent-day (or both) has to move by an order of magnitude (ECON-05, section 8).
7. Illness and hunger have no effect on work: `work_capacity` / `effective_work_capacity` are computed and read by nothing (ECON-06). Pharmacy treatment has no tool (ECON-12). Pensions and government salaries are never paid (ECON-09, ECON-10). Money is not inherited on death (ECON-08).
8. `check_debts` is empty because genesis debts live only in persona prose; nothing seeds `world.loans` from `NeighbourOpinion(kind="debt")` (ECON-13). Head-tax debt cannot be repaid by any tool (ECON-14). Upkeep shortfall is never recorded as debt (ECON-15). A single `eat` wipes the hunger ladder (ECON-16).
9. Residents are told wrong prices: `quote_action_cost("world_mine")` answers 0 because the prefix is never stripped; dispatch actually charges 3 + 5/unit (ECON-17). `move` is always 1 sim because the registry never resolves distance (ECON-18).
10. `control_bakery.py`, `stakes_report.py`, `broadcast_law_of_the_town`, `treat_at_pharmacy`, `pay_pension`, `settle_inheritance`, `repay_head_tax_debt`, `file_head_tax_delinquency`, `head_tax_view`, `effective_work_capacity`, `summarize_household_upkeep` and the resident-side birth request have no production caller.

---

## 1. Inventory

Status key: LIVE = on the `world.run_genesis` embedded path (the only path any run has used) or the `world.main`/`wiring.py` path; MAIN-ONLY = reached only from `world/main.py` via `wiring.py`; TEST-ONLY = imported only by tests; ORPHANED = no production caller anywhere under `app/world`, `app/game`, `app/research`, `app/broker`.

### 1.1 `world/ledger/`

| Path | Lines | Purpose | Status and proof |
|---|---|---|---|
| `world/ledger/__init__.py` | 104 | package exports | LIVE (re-exported names used everywhere) |
| `world/ledger/money.py` | 96 | constants: `TOTAL_USD_CENTS` (18), `SIM_PER_USD=10_000` (43), `SIM_PER_USD_CENT=100` (60), pension pct/age (65-67), account ids (76-78), `sim_to_usd_cents` (81-96) | LIVE: imported by `core.py`, `life/constants.py:24`, `soul_seed.py`, `civic.py` |
| `world/ledger/models.py` | 133 | `Currency`, `AccountKind`, `TransactionReason` (36-65), `Account` (68), `Transaction` (94, `on_behalf_of` 117), `LedgerEvent` | LIVE |
| `world/ledger/errors.py` | 126 | ledger exceptions incl. `LedgerInvariantViolation` (tripwire), `LedgerHaltedError` | LIVE |
| `world/ledger/core.py` | 822 | `Ledger`: `genesis` 189-251, invariant 255-303, `apply_movement` 311-393, `move` 395-426, `resolve_usd_payer` 453-464, `debit_agent_usd` 466-492, `exchange_sim_for_usd` 496-552, `burn_for_inference` 556-578, `mint_sim` 582-608, `pay_wage` 612-634, `collect_tax` 636-648, `register_business` 650-687, `pay_pension` 689-717, `settle_inheritance` 721-760 | LIVE for genesis/move/exchange/burn/register_business (callers: `run_genesis.py:3366,2878-2915`, `registry.py:488`, `civic.py:181`, `labour.py:189`, `commerce.py`). **`pay_pension` ORPHANED** (only caller is its own module; grep over `world/` excluding tests finds none). **`settle_inheritance` ORPHANED** (only `core.py:721,771`; `apply_death` at `run_genesis.py:5012-5034` settles land and tools, never money). `collect_tax` ORPHANED (head tax uses `move` directly, `head_tax.py:411`). |
| `world/ledger/action_costs.py` | 1224 | `SCHEDULE_TABLE` 528-944, `DEFAULT_RULE` 953, `ActionActor` 242-274, `posted_trade_value` 389-415, `SKILL_FOR_TOOL` 428-435, `proficiency_from_reps` 445-458, `proficiency_for` 475-485, `compute_cost` 499-515, `ActionCostSchedule.charge` 1102-1142, `refund_action` 1178-1211, `charge_for_action` 1214-1224 | LIVE: `registry.py:481-497` charges before every handler, `registry.py:505-513` refunds on raise, `bootstrap.py:345,355` publishes the schedule into tool descriptions |
| `world/ledger/head_tax.py` | 608 | `HEAD_TAX_BASE_SIM=1000` (118), `head_tax_sim_per_head` 162-171, `read_tax_rate_bps` 174-184, `HeadTaxDebtStore` 252-360, `charge_daily_head_tax` 363-459, `repay_head_tax_debt` 462-520, `head_tax_view` 523-542, `file_head_tax_delinquency` 569-608 | `charge_daily_head_tax` and `HeadTaxDebtStore` LIVE (`run_genesis.py:3011-3014`, `4688-4747`). **`repay_head_tax_debt`, `head_tax_view`, `file_head_tax_delinquency` ORPHANED** (grep: only defined in this module). `read_tax_rate_bps` ORPHANED (runner reads the policy itself, `run_genesis.py:4694-4695`). |
| `world/ledger/tests/*` | 15 files | unit tests | TEST-ONLY, all pass |

### 1.2 `world/life/`

| Path | Lines | Purpose | Status and proof |
|---|---|---|---|
| `world/life/__init__.py` | 25 | docstring | LIVE |
| `world/life/models.py` | 333 | `AgentLifeRecord` (107), `DormancyState` (89), `Household`, `BirthRequest`, `PopulationDials` (178), `GenesisAgent` (208), `SmallBusiness.opening_stock` (305), `Genesis` | LIVE |
| `world/life/constants.py` | 132 | incidence 28-33, work capacity 51-57, hazards 67-75, pharmacy 78-80, dormancy/revival 86-95, aging 100-102, blessing 107, wedding 112, genesis wallets/sim 116-128, band 132 | LIVE (read by health/death/upkeep/civic) |
| `world/life/clock.py` | 38 | `GameClock` total-hours scalar | LIVE (`run_genesis.py` `LifeGameClock`) |
| `world/life/ports.py` | 71 | `EconomyPort`, `GuardianshipPort` protocols | LIVE via `world/tools/economy_bridge.py:52-116` |
| `world/life/genesis.py` | 1584 | population generator, personas, opinions incl. debts (`make_opinion_pair` 699-780, debt amount 720), wallets (1493-1494), treasury remainder (1521-1531) | LIVE: `run_genesis.seed_genesis` parses `RichGenesis` (`run_genesis.py:3349`); generator itself is an offline CLI |
| `world/life/namepool.py` | 1348 | name/persona text pools | LIVE (genesis) |
| `world/life/soul_seed.py` | 336 | `law_of_the_town_rules` 132-203, `build_soul_seed` 206-233, `build_persona_prompt` 236-286, `default_broadcast_message` 289, `broadcast_law_of_the_town` 299-336 | `build_persona_prompt`/`build_soul_seed` LIVE (`run_genesis.py:3869-3878` writes `persona.txt`). **`broadcast_law_of_the_town` and `default_broadcast_message` ORPHANED** (no console or API caller). |
| `world/life/household.py` | 388 | `marry` 81-157, `come_of_age` 230-388 | LIVE: `social.py:328` (`marry` tool), `social.py:362` (`come_of_age` tool), `run_genesis.py:5113+` (`bring_of_age`) |
| `world/life/upkeep.py` | 510 | constants 81-105, `compute_household_bill` 167, `collect_household_upkeep` 243-327, `HungerState` 330, `effective_work_capacity` 342-355, `advance_hunger` 358-423, `run_daily_upkeep_tick` 435-484 | `run_daily_upkeep_tick` LIVE (`run_genesis.py:4960-4995`). **`effective_work_capacity`, `summarize_household_upkeep`, `earner_burden` ORPHANED** (no reader outside the module). |
| `world/life/health.py` | 153 | `roll_daily_health` 66, `advance_recovery` 108, `work_capacity` 121, `treat_at_pharmacy` 128 | roll/recovery LIVE (`run_genesis.py:4618-4640`, `wiring.py:1161`). **`work_capacity` effectively ORPHANED** (only reader is the orphaned `effective_work_capacity`). **`treat_at_pharmacy` ORPHANED** (no tool; grep of `world/tools/primitives` finds no pharmacy handler). |
| `world/life/death.py` | 145 | `check_*` 13-79, `mark_dormant` 81-93, `check_dormancy_death` 95-104, `revive` 107-136 | LIVE: `run_genesis.py:4911-4959` (`mark_the_broke_dormant`), `4607`, `wiring.py:1204`, `upkeep.py:400-403`, `living.py:241-371` (`revive` tool) |
| `world/life/birth.py` | 165 | `submit_birth_request` 50, `grant_birth_request` 79, `deny_birth_request` | MAIN-ONLY for submit (`wiring.py:1292`); grant via `admin_api.py:857-893` (god console). **No resident tool can request a birth on either path, and the embedded runner has no submit route** (grep `birth` in `run_genesis.py` finds only counters, 1733-2062). In practice ORPHANED. |
| `world/life/aging.py` | 49 | `advance_age` 19-49 | LIVE (`run_genesis.py:4599`, `tick.py:76`) |
| `world/life/population.py` | 65 | `roll_death` 20, `evaluate_birth_request` 39, `is_below_target_band` 63 | `roll_death` LIVE via death.py; **`is_below_target_band` ORPHANED** |
| `world/life/tick.py` | 94 | `apply_death` 31, `run_daily_health_tick` 49, `run_hourly_life_tick` 76 | MAIN-ONLY (`wiring.py:1149,1161`). The embedded runner re-implements both inline (`run_genesis.py:4593-4645`) rather than calling them. |
| `world/life/tests/*` | 15 files | unit tests | TEST-ONLY, all pass |

### 1.3 Other in-scope modules

| Path | Lines | Purpose | Status and proof |
|---|---|---|---|
| `world/control_bakery.py` | 256 | six-rule scripted bakery through `registry.execute` | **ORPHANED**: `grep -rln ControlBakery` outside tests returns only itself; `run_genesis.py` never constructs or ticks it |
| `world/stakes_report.py` | 437 | task label, price/stakes/wage/time/diffusion/learning report sections from events | **ORPHANED from production** (only `world/tests/test_stakes_report.py`); `docs/runs/run-23-report.md:682-696` prints "Prices/Stakes/Wages" sections but from a different generator (inferred: the report says "read from `transactions`", which this module never does) |
| `world/run_genesis.py` (scope parts) | 5492 | `attach_eating` 3302-3331, ledger seeding 3366-3476, `wallet_line` 3513-3553, `call_budget_for` 3555, `persona.txt` 3869-3878, ledger routes 2878-2915, `run_life_ticks` 4565-4686, `charge_the_day_tax` 4688-4750, `reconcile_wages` 4751-4796, `assert_the_day_was_charged` 4797-4820, `mark_the_broke_dormant` 4911-4959, `run_daily_upkeep` 4960-5010, `apply_death` 5012-5032, estate 5033-5110, `bring_of_age` 5113-5160 | LIVE (production root) |
| `world/wiring.py` (scope parts) | 1498 | `advance_tick` life ticks 1149-1166, `burn_inference` dormancy 1196-1210, birth 1292-1330 | MAIN-ONLY; carries hourly/daily health only, no upkeep, no head tax, no wages, no zero-wallet sweep (grep `upkeep|head_tax|accrue_wages|mark_the_broke` in `wiring.py`: no matches) |
| `broker/ledger.py` | 304 | `HttpLedgerClient` (93-166): `GET /ledger/payer/{id}`, `POST /ledger/debit`; `InMemoryLedgerClient` mock | LIVE (broker service); served by `run_genesis.py:2878-2915` and `world/main.py:262-317` |
| `broker/spend.py` | 133 | process-wide `SpendGovernor` (cap default $400, warn 80%) | LIVE |

---

## 2. How it works today (the live `run_genesis` path)

### 2.1 Question 1: the two currencies end to end

**USD (energy).** Genesis: `Ledger.genesis` credits the treasury and each agent from `MINT_ACCOUNT_ID` and records the closed total as `expected_total_usd_cents = treasury + wallets` (`core.py:217-251`); the runner passes each cast member's `usd_wallet_cents` (`run_genesis.py:3366`). The invariant is checked after every USD movement inside `move()` (`core.py:423-424`), and `check_invariant` halts the ledger on any difference (`core.py:268-280`).

Per model call: the engine's herald hits the broker; the broker pre-checks the payer's balance against a ceiling estimate (`broker/service.py:535-563`, `broker/pricing.py:100-113`), dispatches, then debits the exact cost through `POST /ledger/debit` (`broker/ledger.py:142-166`). That route calls `ledger.burn_for_inference(agent_id, cents, tick)` (`run_genesis.py:2892-2915`), which resolves the payer through `resolve_usd_payer` so a minor's cost leaves the parent's wallet with `on_behalf_of=<minor>` on the transaction (`core.py:453-492`, `556-578`). The USD moves to `system:burned` and never returns. Parent-debit is therefore server-side and only depends on `Account.is_minor`/`parent_id`, which `seed_genesis` sets from `GenesisAgent.sponsor_parent_id` (inferred from `open_agent`'s signature, `core.py:114-142`) and `end_parent_billing` clears at 18 (`core.py:144-168`, called by `bring_of_age` via `LedgerGuardianshipPort`, `run_genesis.py:5130`).

**Sim (money).** Non-conserved by design (`ledger/__init__.py` docstring). Sources: genesis `sim_units` set directly on accounts (`run_genesis.py:3427-3428`, businesses `3447-3460`), `mint_sim` for blessings (`core.py:582-608`, called only from the god-console grant path). Sinks: every action cost (`action_costs.py:1134-1141`) goes to the treasury; upkeep (`economy_bridge.py:64-76`), wedding, pharmacy, revival and prayer donations all go to the treasury; head tax goes to the treasury (`head_tax.py:411-420`); `buy_energy` sends sim to the treasury and USD out of it (`core.py:531-548`). Between residents: `transfer` (`commerce.py:290-355`), `trade`, `buy`/`sell` at posted prices (`commerce.py:101-200`), `pay_wage` from a business account (`labour.py:189-197`), `give` (goods only).

**`buy_energy`** (`civic.py:336-209 of that block`, spec gated at `LOCATION_GOVERNMENT_COMPLEX`, `civic.py:425`): minimum 100 sim (one cent), validates both legs, `exchange_sim_for_usd` at 100 sim/cent, emits `economy.energy.bought`. The treasury pays the USD, so the government store can only sell energy while the treasury holds dollars (genesis_20: $880 in treasury, `genesis_20.json` usd_ledger).

**`revive`** (`living.py:241-371`): payer must stand where the dormant target is, hold 10,000 sim; `life.death.revive` debits sim to treasury and credits the target `REVIVAL_USD_CREDIT_CENTS = 100` ($1) from the treasury (`death.py:107-136`, `economy_bridge.py:89-98`). Dormancy flag cleared; the 24-hour clock resets.

**Where the energy line is computed.** `GenesisRunner.wallet_line` (`run_genesis.py:3513-3553`) calls `cost_disclosure.gather_wallet_and_burn` (`cost_disclosure.py:431-476`: payer's USD balance, agent's sim, sum of `INFERENCE_BURN` transactions attributed to the agent in the last `DEFAULT_BURN_WINDOW_TICKS`) and `estimate_ticks_until_dormancy` (`479-496`), and returns `usd_wallet_cents`, `sim_balance`, `last_turn_usd_cents`, `runway_game_days`, `ran_out_after_steps`. `retrieval_depth.format_volatile_line` (`retrieval_depth.py:231-290`) renders it as "Energy: $x in your wallet ... about N game-days" and would also print upkeep/head-tax due (`276-279`) if the dict carried `upkeep_due_sim`/`head_tax_due_sim`; `wallet_line` never sets those keys (ECON-20). The per-tick call budget is also derived from the wallet here (`run_genesis.py:3555`, `world/call_budget.py:46-47`, 2 to 12 calls).

### 2.2 Question 2: stakes, cadence, shortfall, dormancy

`run_tick` runs `dispatch_due_agents` then `run_life_ticks` every tick (`run_genesis.py:4283-4284`). Inside `run_life_ticks` (`4565-4686`):

- **Every tick**: `mark_the_broke_dormant` (`4911-4959`): any alive, non-dormant agent whose own USD balance is `<= 0` is marked dormant, its `keep_doing` commitment is dropped, and `health.agent.dormant` is published to its household with location and the 24-hour deadline. (The `wiring.py` root does the equivalent inside `burn_inference`, `wiring.py:1196-1210`, checking the payer after each burn.)
- **Every game-hour** (`4593-4612`): `aging.advance_age` (minors age 1 year per hour); on `agent.aged_to_adult` the runner calls `bring_of_age` (ends parent billing, default STAY_AND_CONTRIBUTE); `check_dormancy_death` kills anyone dormant for 24 game-hours (`death.py:95-104`).
- **Once per game-day, when `timestamp.game_day != counters.last_life_day_processed`** (`4614-4686`; initial value `-1`, `run_genesis.py:1741`): per agent, `advance_recovery`, terminal/chronic/elderly death rolls, `roll_daily_health`; then `run_daily_upkeep` (`4660`), `charge_the_day_tax` (`4674`), `reconcile_wages` (`4675`), intention expiry, `assert_the_day_was_charged` (`4684`).

Because the counter starts at `-1`, the daily block fires on the **first tick of the run** for day 0, and again at each real boundary (tick 144, 288, ...). Run 23 is the proof: Bram's genesis 3,200 sim reads 2,750 at 06:10 (`docs/runs/run-23-report.md:1291`), Wren's 3,800 reads 3,350 at 06:00 (`:2846`); 450 = 5 heads x 90 = one `h_quarry`/`h_field` bill (`upkeep.py:81-86`; household sizes from `genesis_20.json`). So upkeep has fired in every run since it was wired; the report's "No day boundary was reached, so no upkeep ... charged. Read from `transactions`" (`docs/runs/run-23-report.md:688`) is wrong because upkeep debits are `TransactionReason.TRANSFER` with memo `daily_upkeep` (`economy_bridge.py:64-76`, `upkeep.py:95`).

**Upkeep** (`upkeep.py:435-484` per household): bill = 90 sim per alive member (food 40 + shelter 30 + heat 20). `collect_household_upkeep` (`243-327`) debits earners in list order, each paying as much of the remaining bill as their balance allows, falling back to dependents if there is no earner; partial payment is kept and the shortfall is not recorded anywhere except the `economy.upkeep.defaulted` event payload. `advance_hunger` (`358-423`): paid in full resets the streak; otherwise streak+1; at 2 unpaid days a SHORT ailment "malnutrition" is onset; at 3 unpaid days `mark_dormant` is called directly (the same dormancy the USD path uses), after which the hourly clock kills in 24 hours. Note: `eat` resets the streak to 0 regardless of payment (`run_genesis.py:3325-3329`, see ECON-16). Agents with `household_id=None` are skipped by upkeep (`4975-4980`) but billed by head tax (`4704-4711`).

**Head tax** (`charge_the_day_tax`, `4688-4750`): reads `GovernmentPolicy.tax_rate_bps` (genesis default 0, `charter/models.py:260`); groups alive agents by household; `payer = heads[0]` (`4720`) i.e. the first agent in dict order, not the richest earner; `charge_daily_head_tax` moves `1000 * bps // 10000` sim per head (`head_tax.py:162-171`), so at 0 bps it moves nothing (`407`); unaffordable heads accrue in `HeadTaxDebtStore` and are mirrored into `world_state.arrears[payer]["head_tax"]` (`4741-4742`) which `check_debts` reads (`state.py:978-993`). `assert_the_day_was_charged` (`4797-4820`) halts the run only if the head-tax household count is short; it does not check the amount, and does not check upkeep.

**Is dormancy set by the ledger at $0?** Not by the ledger itself (`Ledger` has no hook); by the runner's per-tick sweep at `usd_balance <= 0` (`4927`), and on the `main.py` root by `burn_inference` after each debit. The upkeep ladder sets it directly at 3 unpaid days. Death follows 24 game-hours later via `check_dormancy_death`, gated by `roll_death` and the `PopulationDials` (see ECON-21 for why the god dials are ignored on this path).

### 2.3 Question 3: action costs

`ToolRegistry.dispatch` charges before the handler (`registry.py:471-497`): `proficiency_for(world, actor, tool)` picks the highest of the seeded `SKILL_FOR_TOOL` skill and any authored skill naming the tool (`action_costs.py:461-485`); `posted_trade_value` resolves the 1% base for `buy`/`sell` off the price board (`389-415`); `charge_for_action` computes `max(min_cost, discount(base + per_unit*qty + value*bps/10000))` (`499-515`) and moves it to the treasury (`1134-1141`), or raises `ActionUnaffordableError` before touching the world (`1131-1133`). A handler exception triggers `refund_action` (`registry.py:505-513`, `action_costs.py:1178-1211`). After a successful built-in labour call the registry bumps `skill_reps` and proficiency rises on a square-root curve to 100 at 60 reps (`registry.py:521-529`, `action_costs.py:442-458`); the discount is linear to 50% at 100 (`488-496`). `keep_doing` repetitions go through `registry.execute` and are charged identically (`state.py:1140`).

Table highlights (`action_costs.py`): harvest 2+3/unit (534), mine 3+5/unit (538), fell 2+4 (542), craft 6+8/batch (546), cook 5+6 (550), build 25+15/tile (554), steal 5 no discount (559); buy/sell 1% min 2 (566-573), transfer 0.5% min 1 (574), `trade` flat 2 (607), store/withdraw 1+1/unit; say 1 (631), rest 1 (627), eat 0 (619), give 0 (623), keep_doing 0 (641), find_people/describe_person 1; move 1 + 2/tile (772); look tools 0 (784-794); vote 2, petition 100, register_org 3,000 (822), file_complaint 10, hire/fire 10 (830-837), transfer_tile/auction 20, set_will 15, subpoenas 10-25, patent decisions 10-20, marry 30 + the 1,000 temple fee charged by the handler (752), come_of_age 20, pray 3, teach_skill 15; set_wage/pay_wage/check_debts/post_price 0/0/0/1 (710-735); revive 0 (737), buy_energy 0 (744); all Part-D seat tools 0 except convene 2 and arrest 2; all C1 conveyance 0; toolify/skillify/revise 0; `DEFAULT_RULE` 5 for anything unlisted (953).

**Why mine "costs 0" and move "costs 1" in run 23.** (a) `quote_action_cost` was called with `tool_name="world_mine"` (`docs/runs/run-23-report.md:1530`); `ActionCostSchedule.quote` has no row for that string and returns `None`, which `quote_action_cost` renders as `sim_cost=0, "no published sim-currency charge"` (`cost_disclosure.py:323-325`). The dispatch charge uses the bare name and did cost 3 + 5/unit. The resident was told a price that is wrong by construction (ECON-17). (b) `move` is charged at its flat minimum because the registry never passes `distance_tiles` (`registry.py:488-497` builds `ActionActor(actor_id, skill_proficiency=..., trade_value_sim=...)` only), so `extract_quantity` returns 0 tiles (`action_costs.py:344-345`) and the per-tile rate is dead (ECON-18); `docs/runs/run-23-report.md:5312` shows the 1-sim quote.

**Known double-charge tools** (module docstring `action_costs.py:149-177`): `register_business` (0 here, 5,000 in `civic.py`), `define_tool` (0 here, charter patent fee in `meta.py`), `marry` (30 here AND 1,000 via `household.marry`, deliberate). `register_org` is charged only here (3,000). `revive` and `buy_energy` are 0 here because their handlers move the real amount. These are consistent; no actual double charge exists today. One description mismatch: `buy_energy`'s tool text says "Cost: 2 sim-currency" (`civic.py:421`) while the schedule row is 0 (ECON-27).

### 2.4 Question 4: wages and jobs

- `hire_employee` / `fire_employee` (`civic.py:244-287`): add/remove an id in `Business.employee_agent_ids`; grants BUSINESS-namespace tool visibility; pays nothing; 10 sim paperwork.
- `set_wage` (`labour.py:86-152`): owner only, at the premises; records `WagePromise(wage_sim, every_days)` keyed `business|employee` (`models.py:66-103`); emits `business.wage.promised` to both parties.
- Accrual: `reconcile_wages` at the day boundary calls `WorldState.accrue_wages` (`state.py:880-906`), adds `periods * wage_sim` to `accrued_sim`, then publishes `business.wage.overdue` for every promise with `owed_sim > 0` (`run_genesis.py:4766-4792`). **The world never pays** (`labour.py:10-16`, by design).
- `pay_wage` (`labour.py:155-239`): owner or staff, at the premises, from the business account (`ledger.move` reason WAGE); overpayment is recorded as `gift_sim`.
- `check_debts` (`labour.py:242-253`, `state.py:933-976`): wage promises both sides, `loans`, and `arrears` rows.
- Government jobs: `GovernmentActions.create_job/cut_job/hire_police` (`charter/government.py:163-236`) create `Job(salary_sim)` records (`charter/models.py:407-430`); no tool exposes `create_job` to a seat except `appoint_police` (`institutions.py:227`); **nothing ever pays `salary_sim`** (grep for `salary_sim` payers: none).
- Pensions: `Ledger.pay_pension` exists (`core.py:689-717`) with policy fields `pension_private_pct/government_pct/eligibility_age` (`charter/models.py:262-264`); **no caller**. `last_salary` is tracked nowhere.
- Tuition: `teach_skill` has a `price_sim` (`social.py:461`); moved between agents; no institution (college) charges anything; `Liora` in run 23 talks about tuition she is "131 sim short" of, which is persona fiction (`docs/runs/run-23-report.md:565`).
- Rent: nothing. Tiles are owned (`property.py`) but there is no tenancy, no landlord, no rent obligation; `UPKEEP_SHELTER_SIM_PER_DAY=30` goes to the treasury, not to any owner.
- Contracts/tenders: `post_tender/award_tender/cancel_tender` exist in `charter/government.py:318-370`; no tool and no payment on award (inferred from the absence of any `ledger` call in that file's grep output).
- Control bakery (`control_bakery.py`): pays a fixed 40-sim wage on the day boundary if the till covers it (`178-199`); never instantiated by the runner.

What pays automatically: nothing sim-positive. Upkeep, tax and action costs are automatic and negative. Wages, pensions, salaries, tenders, tuition, rent all need a manual `pay_wage`/`transfer` by a resident, and three of them have no mechanism at all.

### 2.5 Question 5: debts

Stores today: (1) `HeadTaxDebtStore` in the runner (`run_genesis.py:3011-3014`), event log + balance; (2) `world_state.arrears[agent]["head_tax"]` mirror written by the runner (`4741-4742`), read by `check_debts`; (3) `world_state.loans` keyed `lender|borrower`, written only by `transfer(reason="loan")` and reduced only by `transfer(reason="payment")` (`commerce.py:322-333`, `state.py:908-931`); (4) `wage_promises` (`state.py:281`). All four are `OWNED_STATE_FIELDS` and checkpointed except the runner's `HeadTaxDebtStore` (registered nowhere as `Checkpointable` on this path; inferred from grep for `head_tax_debts` showing only construction and use).

Why all 20 `check_debts` calls returned empty in run 23: the 400-sim and 260-sim debts exist only as `NeighbourOpinion(kind="debt", sim_amount=400)` prose in the persona (`genesis_20.json`, `genesis.py:717-722`); `seed_genesis` never writes a `LoanRecord` from them; day-0 head tax was 0; `arrears["upkeep"]` is never written. The store was empty because nothing had ever been put in it (ECON-13, ECON-15). The double payment then follows: Bram transferred 450 with (inferred) `reason="payment"` which `record_repayment` applied to a loan that did not exist (returns 0, `state.py:926-928`), Ainsley transferred 400 separately, and no tool could tell either of them the debt was already cleared.

### 2.6 Question 6: supply and demand today

- Price formation: a posted-offer board only (`state.py:463`, `post_price` `commerce.py:604-660`), one ask/bid per holder per item, visible where the holder stands or at the premises (`state.py:1019-1042`); `buy`/`sell` settle at the posted number; `trade` settles at a spoken number. No order book, no clearing, no elasticity, no reference price. Genesis posts nothing on purpose (E1).
- Production: every recipe is exactly one input item to one output item per batch (`recipes.py:43-60` `Recipe(input_item, output_item)`, e.g. 70-130); labour cost per batch is the only marginal cost; yields per `harvest` are whatever quantity the caller asks up to the zone's `remaining_yield` (10,000 units per zone, regen per tick, forest infinite; `resources.py:30-80`). There is no seasonality, no crop cycle, no time-to-grow.
- Spoilage/stock: none in the world (`grep -i spoil` across `world/` finds only `world/rules/loader.py:63`, which validates `village_rules.json`'s `market.storage.spoilage_per_day` for the **browser viewer's** separate simulation, loaded by `game/server.py` and `build_rules.py`, not by any world module). Inventory is unbounded and never decays, so food can be hoarded forever.
- Scarcity: only via zone depletion, which at 10,000 units and 20 agents never happens.
- Government levers: `tax_rate_bps` wired to head tax; `credit_store_margin_bps` exists in policy (`charter/models.py:261`, setter `government.py:99`) but `buy_energy` never reads it (`civic.py:181-183` calls `exchange_sim_for_usd` at the fixed rate), so the "credit-store margin" DESIGN §7 names is a dead lever; price controls, rationing, subsidies, public works do not exist as mechanics.

`village_rules.json` (`world/rules/`) already contains a demand model (elasticity 0.6, base prices, `units_per_worker_day`, `demand_per_resident_day`, essential flags) and is validated by `world/rules/loader.py`, but no world-side code consumes it. That is the obvious substrate for reference demand (section 5).

### 2.7 Question 7: life

Live on the runner: daily incidence rolls (4% short, 1% medium, 0.25% long, 0.08% terminal, `constants.py:28-33`), recovery, three death paths, 85+ hazard, aging (minors 1 year per game-hour: the four 15-17-year-olds in genesis_20 become adults within 1 to 3 game-hours of dawn), coming of age (parent billing ends, default STAY), marriage tool (fee 1,000 to treasury, joint household), dormancy sweep, 24-hour death, estate settlement of land and invented tools/skills (`5033-5110`).

Not live: treatment (no tool for `treat_at_pharmacy`; `places.py:43` tells residents the pharmacy sells treatment), work capacity (computed, never read), money inheritance (`settle_inheritance` never called: a dead agent's USD and sim stay in an un-closed account and continue to count toward the invariant), pensions, birth requests (no tool; embedded runner has no submit route; the god console's grant reads a dict nothing fills), population dials (the runner builds a fresh default `PopulationDials` each tick at `4574-4576` instead of `self.population_dials`, so the console's `death_hazard_multiplier` and band never reach the rolls), the law-of-the-town broadcast.

### 2.8 Question 8: genesis balance as shipped

`genesis_20.json`: 20 agents, wallets **$6.00 each** ($120 total, treasury $880), sim 200-5,200 per agent (mean 2,345; household totals 9,800-14,200), businesses 4,000-8,000 sim with opening stock (bakery 12 bread + 20 flour, mill 24 flour + 30 wheat, grocery 74 food units). `genesis_50_town.json`: 30 wallets at $2, 20 at $6; sim median 5,000. `genesis.json` (314): $2 and 5,000 sim flat. Upkeep 90/head/day gives the four 5-person households 22-32 days of runway with zero income and zero tax. See section 8 for the recommendation.

### 2.9 Question 9: the persona text

`persona.txt` = `build_persona_prompt(agent) + "\n\n" + build_soul_seed(agent)` (`run_genesis.py:3875`). The persona block (`soul_seed.py:236-286`) renders bio, tension, values, hard limit, ambition, behaviour under scarcity/strangers/family, competence/blind spot, register, tics, rhythm, four attitudes, secret, then the named opinions with shared facts and private notes (debts carry the amount in prose), then "You are X ... never as a generic assistant". The law block (`206-233`) numbers seven rules (`163-203`): thinking is billed in USD; you can see your balance; nothing is guaranteed; dormancy then death in 24 hours; the store's fixed 10,000:1 rate; only tool calls reach the world; survival of the fittest; then the real starting balances. Law last is documented as load-bearing (`docs/SYSTEM_REVIEW.md:52-56`, eight of 37 L1 scenarios). Assessment in section 3 (ECON-24) and section 4.

---

## 3. Findings

Severity: P0 blocks the target or corrupts money/data; P1 major gap; P2 defect; P3 polish. Layer: world / registry / seam / broker / frontend / engine-finding.

### ECON-01 (P0) Head tax bills 0 sim because the genesis rate is 0, and the S1 gate cannot tell
- `charter/models.py:260` `tax_rate_bps: int = 0`; `head_tax.py:171` `HEAD_TAX_BASE_SIM * bps // 10_000` = 0; `head_tax.py:407` skips every move when `sim_per_head == 0`; `run_genesis.py:4739-4747` still counts the household as "charged"; `assert_the_day_was_charged` (`4797-4820`) compares counts only.
- Why it matters: DESIGN §3 "taxed for existing"; the tax is the government's revenue, the complaints desk's cause, and the thing an election is about. Today the treasury earns only action-cost pennies and upkeep, and "day charged" is true with zero money moving.
- Fix (world, S): set the genesis policy to a real rate (`charter/genesis.py`, e.g. 300 bps = 30 sim/head/day) and make `assert_the_day_was_charged` require `sum(result.total_paid_sim + total_new_debt_sim) > 0` when the rate is non-zero and any alive head exists; add the same assertion for upkeep (`amount_paid_sim + shortfall_sim == bill`).

### ECON-02 (P1) Day-0 upkeep is charged at the first tick of every run, silently
- `run_genesis.py:1741` `last_life_day_processed: int = -1`; `4614` `if timestamp.game_day != self.counters.last_life_day_processed`; run 23 balances at 06:00-06:10 are exactly genesis minus one household bill (`docs/runs/run-23-report.md:1291,2846`; `genesis_20.json` n01 3200, n06 3800).
- Why: every run starts by taking 450 sim from whoever is first in each household before anyone has acted, the report says it never happened, and every "runway" figure in the run docs is off by a day. A resumed checkpoint keeps the counter, so a restart does not double-bill, but a fresh boot always bills day 0 at 06:00 rather than at 24:00.
- Fix (world, S): initialise the counter to the boot day (`timestamp_for(start_tick).game_day`) or gate the daily block on `is_day_boundary(tick)` (`core/clock.py:75-82`) the way `wiring.py:1156` does; document which convention (bill at dawn vs at midnight) is intended.

### ECON-03 (P2) Upkeep, action costs and refunds are indistinguishable from gifts in the trace
- `economy_bridge.py:64-76` debits with `TransactionReason.TRANSFER` and memo `daily_upkeep`; `action_costs.py:1139` and `1208` also use TRANSFER with memo prefixes; wedding, pharmacy, revival, prayer donations likewise. Only head tax is TAX (`head_tax.py:416`).
- Why: the run report reads `transactions` by reason and concluded "no upkeep charged" (`docs/runs/run-23-report.md:688`); GDP-per-inference-dollar, velocity and Gini cannot be computed from a table where a bill and a handout look alike.
- Fix (world, S): add `UPKEEP`, `ACTION_COST`, `REFUND`, `FEE`, `DONATION`, `REVIVAL`, `ENERGY_EXCHANGE` to `TransactionReason` (`ledger/models.py:36-65`), map `LedgerEconomyPort.debit_sim(reason=...)` strings onto them, and make the report group by reason.

### ECON-04 (P0) Sim-currency is a one-way drain into a treasury that spends nothing
- Every sink lands in `government:treasury` (`action_costs.py:1136`, `economy_bridge.py:68`, `head_tax.py:413`, `civic.py:181` via `core.py:531-538`, `social.py:423-425`). Outflows from the treasury: `refund_action`, `credit_sim`/`credit_usd_cents` for revival, `pay_pension` (never called), `pay_wage` with `TREASURY_ACCOUNT_ID` (never called). genesis_20: 46,900 sim in wallets + 23,000 in businesses; upkeep alone removes 1,800/day (20 x 90), i.e. the entire household float in ~26 days with zero activity, before tax, action costs or energy purchases.
- Why: DESIGN §3 says money is "what work earns", but work only moves existing sim between residents; the only sim faucet is a god blessing. A closed sink cannot thrive; it can only ration decline. This is the structural reason "thriving" is unreachable regardless of agent behaviour.
- Fix (world, M): give the treasury an outflow loop on the day boundary: government payroll for `Job` records (`charter/models.py:407`), pensions (ECON-09), tender payments on `award_tender`, a welfare floor (charter-set), and optionally treasury purchases of essentials at posted bids (public granary). Decide a sim policy explicitly: either (a) fixed supply with a redistributing government, or (b) a mint tied to real production (e.g. the treasury buys harvested grain at a floor price, minting the sim, which makes "money is what work earns" literally true and gives prices an anchor). Track `treasury_sim` and money velocity in the metrics.

### ECON-05 (P0) The exchange rate makes energy unpurchasable from honest work
- `money.py:43` `SIM_PER_USD = 10_000`; measured burn ~$2 per agent-day (run 20, `docs/kasukabewhatismissing.md:287-295`) = 20,000 sim/day; upkeep 90/day; bread 15-30 sim, wheat 10-18 sim (run 23); control bakery wage 40/day (`control_bakery.py:59`). A surplus of 200-300 sim/day buys 2-3 cents of thinking, i.e. one or two model calls.
- Why: the loop "work earns money, money buys energy" is closed on paper and open in practice; nobody in 23 runs has called `buy_energy` and the L1 harness reports 0-20% reach, but even a resident that reached it could not afford a meaningful amount. `revive` at 10,000 sim buys $1 (`death.py:118-121`), roughly two game-hours at the measured rate.
- Fix (world + operator decision, S for the constant, L for the cost): pick numbers so that one working day's surplus buys about one game-day of cognition at the budgeted rate. Section 8 proposes `SIM_PER_USD = 2,000` together with the T8/B3 cost work (target $0.10-0.15 per agent-day), or, keeping 10,000, scaling all sim prices/wages/upkeep by ~5x. Either way `REVIVAL_COST_SIM`, `BABY_BLESSING_COST_SIM`, `TEMPLE_WEDDING_FEE_SIM`, `BUSINESS_REGISTRATION_FEE_SIM`, `register_org` and the schedule table must be re-derived together; `death.py:1117-1120` will assert if the revival/exchange pair drifts.

### ECON-06 (P1) Illness and hunger have no effect on work
- `health.work_capacity` (`health.py:121-125`) and `upkeep.effective_work_capacity` (`upkeep.py:342-355`) are read by nothing outside `upkeep.py`; `WORK_CAPACITY_BY_AILMENT` (`constants.py:51-57`) and `HUNGER_MILD_WORK_CAPACITY_MULTIPLIER` (`upkeep.py:103`) are inert. The registry, `harvest_from_zone`, `run_recipe`, `keep_doing` and the call budget never consult them.
- Why: DESIGN §5 "sickness cuts work capacity"; the hunger ladder's first rung is a no-op; a terminal patient harvests at full rate until the day they die. Treatment (ECON-12) has no economic reason to exist.
- Fix (registry, S): in `ToolRegistry.dispatch` after the charge, for LABOUR-category tools scale the effective quantity by `effective_work_capacity(agent.health, hunger)` (hunger must be reachable from world state, see ECON-16) or refuse with probability `1 - capacity` and a named reason; publish the capacity in the volatile line.

### ECON-07 (P1) Money is not inherited on death
- `run_genesis.apply_death` (`5012-5032`) calls `settle_estate_land` and `settle_estate_tools`; `Ledger.settle_inheritance` (`core.py:721-760`) has no caller. The dead account is never closed, so a dead agent's USD keeps counting as a live wallet in `usd_total` (`core.py:255-266`) and its sim vanishes from circulation.
- Why: DESIGN §5 "estates become inheritance"; `AGENT_LIFE_FRAMEWORK.md` §13 builds a whole worked example on it; wills (`set_will`) name money bequests nobody executes (inferred: `settle_estate_tools` reads `bequest.tools`/`skills` only, `5048-5063`).
- Fix (world, S): in `apply_death`, after land/tools, `await self.ledger.settle_inheritance(agent_id, heirs, tick)` with heirs from the will's money bequests else `living_relatives` (`5082-5096`); business accounts owned by the deceased transfer via `transfer_business` semantics.

### ECON-08 (P1) The `main.py` root has no economy: no upkeep, tax, wages, or zero-wallet sweep
- `wiring.py:1130-1170` runs only hourly aging/dormancy-death and daily health; grep for `upkeep|head_tax|accrue_wages|mark_the_broke|reconcile` in `wiring.py` returns nothing. `burn_inference` marks dormant per debit (`1196-1210`) but the upkeep ladder can never fire there.
- Why: ROADMAP T3 says `world/main.py` becomes the only server; today the two roots have different physics, and the console/viewer contract (`game/console`) targets the root without stakes.
- Fix (world, M): extract the day jobs from `GenesisRunner` (`run_life_ticks`, `run_daily_upkeep`, `charge_the_day_tax`, `reconcile_wages`, `mark_the_broke_dormant`, `bring_of_age`, estate settlement) into one `world/life/day_jobs.py` module taking `(world_state, ledger, debts, hunger, dials, rng, tick)` and call it from both roots; delete the inline copies of `life/tick.py` (`run_genesis.py:4593-4645`).

### ECON-09 (P1) Pensions are never paid; the elderly have no income
- `core.py:689-717` `pay_pension` has no caller; `charter/models.py:262-264` policy fields unread; no `last_salary` field exists on any model.
- Why: DESIGN §3/§4 pensions at 70+; 15% of the 250 cast are retired; the "pension fiscal gap" research metric has no data.
- Fix (world, S): day-boundary job: for each alive agent `>= policy.pension_eligibility_age`, `ledger.pay_pension(agent, age, last_salary, kind, tick)` where `last_salary` is a charter-set flat reference (e.g. 100 sim/day) until wages exist to record; treasury-funded, so it depends on ECON-04.

### ECON-10 (P1) Government jobs, police and tenders create records that pay nothing
- `charter/government.py:163-236` `create_job/hire_police` store `Job.salary_sim`; `318-370` tenders; no ledger movement anywhere in that module (grep `ledger` finds none); only `appoint_police` is a tool (`institutions.py:227`).
- Fix (world, M): payroll in the same day job as ECON-09: every `Job` with `assigned_agent_id` and status ACTIVE pays `salary_sim` from treasury; `award_tender` creates an obligation (ECON-13 design) paid on `build_structure` completion.

### ECON-11 (P2) Population dials are ignored by the live loop
- `run_genesis.py:4574-4576` constructs `PopulationDials(target_band_min=..., target_band_max=...)` fresh each tick; `self.population_dials` (`3055-3058`), which `/admin/god/population-dials` mutates, is never read by `run_life_ticks` or `run_daily_upkeep` (`4993`).
- Fix (world, S): use `self.population_dials`; add a test that setting `death_hazard_multiplier=0` prevents a scheduled terminal death.

### ECON-12 (P1) Pharmacy treatment is unreachable
- `health.treat_at_pharmacy` (`health.py:128-153`) has no tool; `places.py:43` advertises "Buy treatment for short illnesses."; `PHARMACY_SHORT_TREATMENT_COST_SIM=500` inert.
- Fix (registry, S): a `treat_illness` tool gated at the pharmacy: pays 500 sim to the pharmacy business account (not the treasury), calls `treat_at_pharmacy`, and sets `ailment.treated` so `check_chronic_death` (`death.py:32-46`) is meaningful once a hospital exists.

### ECON-13 (P1) Genesis debts never reach the ledger; `check_debts` is empty by construction
- `genesis.py:717-722` creates `NeighbourOpinion(kind="debt", sim_amount=rng.randint(3,60)*100)` on both sides; `seed_genesis` (`run_genesis.py:3349-3476`) writes wallets, businesses, stock, names and bios but no `LoanRecord`; `state.debts_of` (`933-976`) therefore has nothing to show; run 23: 20 calls, 20 empty answers, one debt paid twice.
- Why: the best social material the town produces (HUMAN_BASELINES §5) is invisible to its own instruments; `transfer(reason="payment")` applied to a non-existent loan returns 0 (`state.py:926-928`).
- Fix (world, S): in `seed_genesis`, for every opinion with `kind == "debt"` and `role == "owing"`, `world_state.record_loan(lender=target_agent_id, borrower=agent_id, amount=sim_amount, tick=0)`; skip the mirrored `owed` side. Then the design in section 5 (obligation ledger).

### ECON-14 (P1) Head-tax debt can never be repaid
- `repay_head_tax_debt` (`head_tax.py:462-520`) has no caller; `transfer` requires `to_agent_id in world.agents` (`commerce.py:311-312`), and the treasury is not an agent; `check_debts` tells the resident it owes the treasury (`state.py:986-992`) and its description says "To pay any of it use `transfer` or `pay_wage`" (`labour.py:294-297`), both of which will refuse.
- Fix (world/registry, S): a `pay_tax` tool (or `transfer` accepting `government:treasury`) that calls `repay_head_tax_debt` and decrements `arrears`; wire `file_head_tax_delinquency` after N unpaid days so the complaints desk finally has a case.

### ECON-15 (P2) Upkeep shortfall is not a debt; `arrears["upkeep"]` is never written
- `run_daily_upkeep` (`4960-5010`) publishes the event and updates hunger only; `state.py:474` documents `arrears` as `{"head_tax": n, "upkeep": n}`, but only `head_tax` is ever set (`4741-4742`).
- Fix: accrue `outcome.shortfall_sim` to `arrears[payer]["upkeep"]` (or to the obligation ledger) and let repayment clear hunger consequences.

### ECON-16 (P1) A single bite resets the hunger ladder while the bill stays unpaid
- `attach_eating` (`run_genesis.py:3325-3329`) sets `HungerState(consecutive_unpaid_days=0)` on every `agent.ate`; the tool's own note says "Your household still owes its daily upkeep" (`living.py:117`), but nothing records the debt (ECON-15) and the escalation is the only consequence of not paying.
- Why: with ECON-15, a household can skip upkeep indefinitely as long as each member eats one apple a day; the "doing nothing is also a way to die" rule (`upkeep.py:471-476`) is void.
- Fix (world, S): separate the food and money halves: eating satisfies the food component (40) and clears hunger; shelter+heat (50) remain owed and accrue as an obligation with its own consequence (eviction/heat loss, or just delinquency to the desk). Or keep the ladder for money and make `eat` reduce a separate `meals_missed` counter that drives capacity (ECON-06).

### ECON-17 (P1) `quote_action_cost` tells residents wrong prices for prefixed tool names
- `cost_disclosure.py:323-325`: a `None` from `schedule.quote` becomes `sim_cost=0, "no published sim-currency charge"`; the engine exposes tools as `world:mine`/`world_mine` and residents pass that name back (`docs/runs/run-23-report.md:1530`); the dispatch charge uses the bare name and does charge (`registry.py:481-497`).
- Fix (world, S): normalise `params.tool_name` in `quote_action_cost_handler` (`355-379`): strip `world:`/`world_`, match against `world.registry_tool_names`, and answer "no such tool" for unknown names rather than "free".

### ECON-18 (P2) `move` is always charged its 1-sim floor
- `registry.py:488-497` never resolves `distance_tiles`; `action_costs.py:263,344-345`; the module's own integration note says the caller must (`action_costs.py:135-147`).
- Fix (registry, S): `distance_tiles = world.movement.estimate_travel_ticks(...)` (already used by `move_handler`, per the note) when `tool_name == "move"`.

### ECON-19 (P2) Head-tax payer is "first in dict order", not the household's earner
- `run_genesis.py:4720` `payer = heads[0]`; upkeep pools across earners (`upkeep.py:286-296`) but tax charges one person who may be a child or a pensioner with 200 sim.
- Fix (world, S): reuse `compute_household_bill(...).earner_ids` ordering, or charge each head's own account first and fall back to the household's richest earner.

### ECON-20 (P1) Residents are never shown today's upkeep or tax
- `format_volatile_line` renders `upkeep_due_sim`/`head_tax_due_sim` (`retrieval_depth.py:276-279`); `wallet_line` never sets them (`run_genesis.py:3535-3553`); `cost_disclosure.HouseholdUpkeepView` and `render_economic_context_block` (`499-600`) are unused. DESIGN §3: "today's upkeep and tax, who depends on them ... visible in context every tick".
- Fix (world, S): compute `compute_household_bill` for the agent's household and `head_tax_sim_per_head * heads` in `wallet_line`, plus outstanding arrears, dependents, and the next due tick.

### ECON-21 (P1) The dead zone between broker refusal and dormancy
- Broker refuses dispatch when `balance < ceiling estimate` (`broker/service.py:535-563`; at 15-20K input tokens and 1,536 max output the ceiling is ~1-2 cents); the runner marks dormant only at `usd_balance <= 0` (`run_genesis.py:4927`). An agent left with 1 cent can never think, is not dormant, cannot be revived (`living.py:292-295` requires `is_dormant`), still owes upkeep and tax, and the 24-hour clock never starts.
- Fix (world + broker, S): mark dormant when `usd_balance < DORMANCY_THRESHOLD_CENTS` (e.g. 2), or have the broker's `broker.call.rejected` (insufficient funds) reach the world and trigger `mark_dormant`; publish the threshold in the law block.

### ECON-22 (P2) Two life-tick implementations that already differ
- `life/tick.py:49-94` (used by `wiring.py`) vs `run_genesis.py:4593-4645` inline; the runner adds coming-of-age and a different `apply_death`; `tick.py` does not.
- Fix: ECON-08's extraction.

### ECON-23 (P2) Births are unreachable in every run
- No tool calls `submit_birth_request`; `wiring.py:1292` is the only caller and is MAIN-ONLY; the embedded admin grant reads `extras.birth_requests` that nothing fills (`admin_api.py:857-893`).
- Fix (registry, S): `request_child` tool (married, co-located at home or temple, sponsor named) that creates the request; the god console grants; `grant_birth_request` already charges 5,000 and sets `sponsor_parent_id`. Also spawn the newborn's engine (a runner concern, out of scope here).

### ECON-24 (P2) The soul seed and standing brief state rules without the numbers that make them actionable
- `soul_seed.py:163-203`: rule 1 says every word said is "a real inference call, billed ... in real US dollars" (true of thinking, but `say` costs 1 sim, `action_costs.py:631`, which is never stated); no rule names upkeep (90/head/day), head tax, dependents, the revival fee, that `revive` needs a rescuer standing over you, or that action costs exist at all; the perception standing brief adds "Upkeep is charged to your household every day ..." without an amount (`docs/runs/run-23-report.md:1291`). Balances are given once at genesis and every tick in the volatile line; prices only via `look_around`.
- Why: DESIGN §3 requires "told the prices and the rules"; today they are told the rules and must discover the prices, and the one price they can ask for (`quote_action_cost`) is wrong (ECON-17). The law-last ordering is right and measured; keep it.
- Fix (world, S): add to `law_of_the_town_rules` three numeric rules (daily upkeep per head and that it is billed to the household; that actions cost sim on a published schedule you can query; the dormancy threshold and revival price), and move the "how to eat" sentence out of the volatile brief into the law block so it stops being re-read every tick. Do not add strategy.

### ECON-25 (P2) `control_bakery.py` and `stakes_report.py` are finished and unwired
- No production caller for either (inventory). The control's docstring promises R1-R7 comparison; the report module promises E/S/W/T/C figures; the run report prints its own.
- Fix (world, S): tick `ControlBakery.act` from `run_tick` for a genesis-flagged control business; generate the run report's economy sections from `stakes_report` over the trace's `events` table.

### ECON-26 (P3) Wiring tests that assert on source text cannot fail for the right reason
- `world/tests/test_upkeep_is_wired.py:28-79` asserts substrings (`"run_daily_upkeep" in source`, `"continue" in source and "except" in source`). They pass if the call is commented out inside a string.
- Fix: replace with a behavioural test that boots `make_runner`, sets `current_tick` to 143, ticks once, and asserts a `daily_upkeep` transaction and an `economy.upkeep.charged` event exist (the pattern `test_dormancy_is_reachable.py:112-125` already uses).

### ECON-27 (P3) Description/schedule mismatches
- `buy_energy` description "Cost: 2 sim-currency" (`civic.py:421`) vs schedule 0 (`action_costs.py:744-751`). `check_debts` description promises "upkeep arrears" (`labour.py:294`) that are never recorded (ECON-15) and repayment routes that refuse (ECON-14).

### ECON-28 (P3) `gather_wallet_and_burn` scans the whole transaction log per agent per tick
- `cost_disclosure.py:461-468` iterates `ledger.transactions` linearly; at 250 agents and 144 ticks/day this is O(N x T x calls) and grows without bound. Fix: keep a per-agent ring buffer of burns in the runner, fed from `economy.wallet.debited` events with reason `inference_burn`.

### ECON-29 (P3) Marriage and treatment fees go to the treasury, not to the temple/pharmacy
- `economy_bridge.py:52-76` routes every life-initiated debit to `TREASURY_ACCOUNT_ID`; the pharmacy is a business with an account (`genesis_20.json` b_grocery/b_bakery pattern), the temple has none. Fees that should be income for a keeper or a pharmacist become government revenue.

### ECON-30 (P3) `credit_store_margin_bps` is a policy lever nothing reads
- `charter/models.py:261`, setter `government.py:99-117`; `buy_energy` uses the fixed rate (`civic.py:181-183`). DESIGN §7 lists the margin as a government power. Fix: apply `sim_amount * (10000 + margin_bps) / 10000` before `exchange_sim_for_usd`, with the margin retained by the treasury.

### ECON-31 (P3) Solo residents (no household) never pay upkeep
- `run_daily_upkeep` groups by `household_id` and skips `None` (`4975-4980`); head tax handles them (`4708-4711`). Every genesis resident has a household, and `MOVE_OUT_ALONE` creates one, so this is latent; fix by treating a household-less agent as a one-member household in both jobs.

### ECON-32 (P2) The `$10 run cap` vs `$6 x 20 wallets` makes dormancy unreachable by budget arithmetic
- genesis_20 wallets total $120 (`genesis_20.json` usd_ledger) while the run's spend governor stops at $10; wallets ended $5.25-$5.85 of $6. No resident can reach $0 before the run halts, so the whole stakes chain (dormancy, revive, `buy_energy` need) is untestable in the current run shape. This is a configuration finding, not code; see section 8.

---

## 4. Gaps vs DESIGN.md

| DESIGN.md clause | State |
|---|---|
| §3 "$1000 invariant, halts sim" | Built and live (`core.py:268-303`); the halt reaches the runner through `SpendGovernor.check` reading `ledger.halted` (inferred from `run_genesis.py:4553-4556` comment) |
| §3 "10,000 sim -> $1 at the government store" | Built and live (`buy_energy`); never called in a run; unaffordable at current burn (ECON-05) |
| §3 "Baby blessing 5,000 sim" | Built (`birth.py:79`), unreachable (ECON-23) |
| §3 "Revival 10,000 sim; unrevived 24 h -> death" | Built and live; the 1-cent dead zone bypasses it (ECON-21) |
| §3 "Prices, wages, contracts fully emergent" | Prices: posted-offer board only; wages: promise+manual pay; contracts: none |
| §3 "Businesses registered with government (fee + taxes)" | Fee yes (5,000); business taxes: none |
| §3 "Jobs: government and businesses create/cut jobs; pensions 25%/50% at 70+" | Job records exist, never paid (ECON-10); pensions never paid (ECON-09) |
| §3 "Cognitive poverty: at $0 dormant; revive; death" | Live on the runner; not on `main.py` upkeep path (ECON-08) |
| §3 "Energy is spent on every action" | Actions cost sim (money), not USD (energy). DESIGN's own two-currency paragraph says energy pays for moving and working; the implementation charges sim for actions and USD only for tokens. Built differently, and arguably better (a walk should not burn tokens), but the persona text still tells residents actions are billed in dollars (ECON-24) |
| §3 "Basic tax per head every game-day, rate is policy" | Built, wired, rate 0 (ECON-01); delinquency to the courts orphaned (ECON-14) |
| §3 "daily upkeep charged to the household" | Built and live (with ECON-02/15/16 caveats) |
| §3 "Agents must be able to see ... today's upkeep and tax, who depends on them, and what any action will cost" | Balance and runway: yes. Upkeep/tax/dependents: no (ECON-20). Action cost: wrong for prefixed names (ECON-17) |
| §4 "minors billed to parent; age 1 yr/hour until 18" | Built and live; in a 20-agent one-day run the minors are adults by 09:00 |
| §4 marriage "fee to temple, joint household, enables children" | Fee to treasury (ECON-29); household merge yes; children: no path |
| §5 incidence rates, treatment at pharmacy, death paths, estates | Rates and deaths live; treatment orphaned (ECON-12); money estates orphaned (ECON-07) |
| §5 "population is a god-tab dial" | Dials exist and are ignored by the loop (ECON-11) |
| §7 "credit-store margin" | Policy field, dead (ECON-30) |
| §8 President "address-the-nation" | `broadcast_law_of_the_town` orphaned |
| §10 "GDP per inference-dollar, Gini, currency velocity, pension fiscal gap" | Not computable from the trace as written (ECON-03), pensions absent |

---

## 5. Gaps vs the target

### 5.1 Thriving, not surviving
Exists: a correct ledger, action costs with mastery discounts, posted prices, wage promises, `keep_doing`. Missing: any sim source (ECON-04), an affordable energy price (ECON-05), payroll on a cadence, credit, rent, inheritance of money, savings/interest, insurance, bankruptcy. Build order (section 6): obligation ledger -> treasury outflows -> re-rate -> rent/credit.

### 5.2 Organisations and government running
Exists: `Job`, tenders, permits, policy fields, `set_tax_rate`, `appoint_police`, business accounts, `hire/fire/set_wage/pay_wage`. Missing: payroll for government jobs and police, tender payment, business tax, any treasury budget view for a minister, price controls/rationing/subsidy levers, and the obligation ledger that makes a contract enforceable. Where: `world/life/day_jobs.py` (new), `charter/government.py` (+ ledger calls), `tools/primitives/institutions.py` (tools for create_job, post_tender, award_tender, set_price_ceiling, set_ration).

### 5.3 Expansion by the town's own builders
Out of this review's scope except for money: `build_structure` charges labour and materials (`action_costs.py:554`) but no tender pays the builder; land auctions send proceeds to the treasury (`action_costs.py:845`). Needs: tender award -> obligation -> paid on completion event.

### 5.4 Skills and tools that cut model calls
World-side: proficiency lowers sim cost only (`action_costs.py:488-496`); `keep_doing` is the one mechanism that cuts calls. The cognition discount (engine artifacts) is outside this scope. Economic hook to add: mastery should also raise output per call (yield per harvest, batches per cook), which is the same lever as ECON-06's capacity multiplier, applied upward.

### 5.5 Supply and demand with ideology-driven mechanics
Exists: posted-offer market, finite zones, 1:1 recipes, opening stock, `village_rules.json`'s unused demand model, `GovernmentPolicy`, charter amendment machinery (out of scope), rulesets (T6, not built). Add, in order:
1. **Consumption sinks and decay**: daily food consumption per head (tie to the upkeep food component: eating from inventory satisfies it, otherwise buy); spoilage per day for perishables (`Inventory` needs `age` per lot); tool wear is already a cost. Where: `world/tools/inventory.py`, `life/upkeep.py`.
2. **Reference demand**: the ten control businesses (`genesis.control_businesses`, `ControlBusiness.pricing_strategy`) as scripted price-takers that post bids/asks from `village_rules.json` (`elasticity 0.6`, `base_price_sim`, `demand_per_resident_day`) against observed inventory and recent sales. Residents can undercut or overprice them; the controls provide the floor and ceiling the design's "price-discovery speed" metric needs. Where: `world/control_bakery.py` generalised to `world/control_businesses.py`, ticked from `run_tick`.
3. **A clearing venue**: `market_order(item, side, quantity, limit)` at the marketplace with a per-tick uniform-price match, publishing `economy.market.cleared` with price and volume. Posted boards stay for shops.
4. **Government levers as dispatch-time reads**: `post_price` validates against `policy.price_ceiling[item]`/`price_floor`; `buy` enforces `policy.ration_per_head_per_day[item]`; `policy.subsidy_bps[item]` makes the treasury pay part of the posted price; `policy.credit_store_margin_bps` in `buy_energy`; `tax_rate_bps` already. Where: `charter/models.py` (fields), the three handlers.
5. **Ideology as mechanics**: a `regime` block on the policy/charter read by handlers: `property="private|collective"` (harvest output credited to a commune account, wages from it), `market="free|controlled|rationed"`, `welfare_floor_sim`, `inheritance="heirs|state"`, `labour="free|conscripted"`. An amendment (3/5 vote) changes the block; the next tool call reads the new rule, exactly as `charge_daily_head_tax` reads `tax_rate_bps` today. ROADMAP T6 rulesets are the general form; the policy block is the minimal version that ships first.

### 5.6 Evolving goals
Out of scope except: the economy must give goals something to move on. `set_intention` exists; nothing links intention to money (e.g. "save 5,000 for a plot"). The obligation ledger gives medium-term goals a concrete object (a debt due on day 4).

### 5.7 World-class instrumentation
Needed in scope: transaction reasons (ECON-03), obligation events, per-day treasury and money-supply snapshot in `GenesisMetrics`, `stakes_report` wired to the trace, ledger snapshot per day (already in checkpoints, inferred), household-level runway series.

---

## 6. Recommended work items

Ordered P0 first. Each: spec, acceptance test, layer, effort.

**W1 (P0) Set a real head-tax rate and make S1 check money, not counts.** `charter/genesis.py` seeds `tax_rate_bps=300`; `assert_the_day_was_charged` requires paid+debt > 0 when rate > 0, and upkeep paid+shortfall == bill. Test: boot 20 agents, tick to 144, assert TAX transactions total `30 * alive_heads` and that a household with 0 sim produced `HeadTaxDebtEvent`s. World, S. (ECON-01)

**W2 (P0) Treasury outflow loop.** New `world/life/day_jobs.py` with `run_day_jobs(...)` called by both roots: upkeep, head tax, wage accrual, **pensions** (flat reference salary from policy), **government payroll** for active `Job`s, tender payouts. Test: an elderly agent's sim rises by `pension_private_pct * reference // 100` at each boundary; an appointed police officer is paid `salary_sim`; the treasury's sim decreases by the sum. World, M. (ECON-04, 08, 09, 10, 22)

**W3 (P0) Re-rate the two currencies together.** Operator decision on `SIM_PER_USD` (proposal: 2,000) and a burn target per agent-day; re-derive revival/blessing/wedding/registration/schedule constants; sized wallets (section 8). Test: with the proposed constants, a scripted farmer doing 20 `harvest` reps and selling at the reference price nets enough surplus in one day to `buy_energy` one day's burn at the target rate; `death.py`'s import-time assertion still holds. World + DESIGN.md, S code / L for the cost work. (ECON-05, 32)

**W4 (P0) Fix the boot-day upkeep and the report's blindness.** Gate daily jobs on `is_day_boundary` (or initialise the counter to the boot day); add `TransactionReason.UPKEEP/ACTION_COST/REFUND/FEE/...`. Test: after `boot()` at tick 0 no agent's sim has moved; after tick 144 every household shows one UPKEEP transaction per payer; the run report's "Stakes" section counts them. World, S. (ECON-02, 03)

**W5 (P1) Obligation ledger.** `WorldState.obligations: dict[str, Obligation]` (owned state, checkpointed) with `Obligation{id, kind: loan|wage|rent|tax|upkeep|fine|tuition|tender, creditor, debtor, principal, outstanding, due_tick, period_ticks, interest_bps, penalty, status, origin, memo}`. Writers: genesis (persona debts), `transfer(reason=loan)`, `set_wage` (period), head tax and upkeep shortfalls, `rule` (fines), `award_tender`. Settlers: `transfer(reason=payment, obligation_id=...)`, `pay_wage`, a new `pay_tax`; `settle(obligation_id, amount)` is the single function. Day job: accrue periodic obligations, apply interest, emit `economy.obligation.overdue` with `involved_agent_ids`, auto-file to the complaints desk after N days. `check_debts` reads it; the volatile line shows `due_today`. Replace `loans`, `wage_promises.accrued/paid`, `arrears`, `HeadTaxDebtStore` by adapters over it. Test: genesis_20 boots with Bram owing Marta 400 and Rosalind 260 visible to all three via `check_debts`; Bram's `transfer(..., reason="payment", obligation_id)` of 400 closes it and a second payment by Ainsley is refused with "already settled". World, M. (ECON-13, 14, 15)

**W6 (P1) Food, hunger and capacity made real.** Split upkeep into food-in-kind and money; `eat` satisfies the food component for the day; unpaid shelter/heat accrues as an obligation; `effective_work_capacity` scales LABOUR quantity at dispatch. Test: a household that pays nothing but eats stays fed, accrues 50/head/day of shelter+heat debt, and works at full rate; one that neither eats nor pays hits the malnutrition ailment on day 2 and its `harvest` of 10 yields 5. Registry + world, M. (ECON-06, 16)

**W7 (P1) Dormancy threshold and inheritance of money.** `mark_the_broke_dormant` at `< 2 cents` (or on broker rejection); `apply_death` calls `settle_inheritance`. Test: an agent at 1 cent is dormant within one tick; a dead agent's USD and sim reach `living_relatives` and the account is `closed`. World, S. (ECON-07, 21)

**W8 (P1) Honest prices and costs in front of the resident.** Prefix-normalised `quote_action_cost`; distance-priced `move`; `wallet_line` carries upkeep/tax/dependents/arrears; law block gains the three numeric rules. Test: `quote_action_cost("world_mine", {quantity: 4})` == 23; the perception of a 5-member household reads "upkeep due today 450, head tax 150, you support 2 dependents". World, S. (ECON-17, 18, 20, 24)

**W9 (P1) Treatment, births, pension dials, population dials.** `treat_illness` tool at the pharmacy (fee to the pharmacy account), `request_child` tool, `run_life_ticks` reads `self.population_dials`. Test: `death_hazard_multiplier=0` from the admin route stops a scheduled terminal death; a married couple's request appears in `/admin/god/blessings` and a grant charges 5,000 and spawns a minor billed to the sponsor. Registry + world, M. (ECON-11, 12, 23)

**W10 (P2) Supply/demand substrate.** Perishable lots with spoilage; per-head daily food consumption; control businesses ticked as price-takers from `village_rules.json`; `market_order` clearing at the marketplace; price-ceiling/floor, ration and subsidy policy fields read by `post_price`/`buy`; `credit_store_margin_bps` in `buy_energy`. Test: with 20 residents and one farmer, the control grocer's bread ask rises when its stock falls below `demand_per_resident_day * population * smoothing_days` and falls back after a restock; a `set_price_ceiling("bread", 20)` makes a 30-sim `post_price` refuse with the policy named. World + registry, L. (section 5.5, ECON-30)

**W11 (P2) Wire the control bakery and `stakes_report`, replace the string-inspection tests.** Test: a run with `--control-bakery` writes a scorecard; `stakes_report.wages(events)` on the run's events equals the report's figures. World, S. (ECON-25, 26)

---

## 7. Genesis balance recommendation (question 8, with reasoning)

Constraints that must hold at once: (1) upkeep must bite within a few game-days for an idle household but not before a working one can respond; (2) a day of honest work must clear the household bill with a surplus; (3) the surplus must buy a meaningful amount of energy; (4) the run's USD budget must be able to exhaust some wallets or dormancy is fiction.

Measured inputs: burn $2.00/agent-day now (run 20), roadmap target $0.10; upkeep 90/head/day; observed prices bread 15-30, wheat 10-18, flour 32-70, eggs 12-14; control wage 40/day; harvest tool wear 5/unit; a `keep_doing` afternoon of ~20 reps.

Proposal (all in one commit, all constants already live in `money.py`, `life/constants.py`, `upkeep.py`, `head_tax.py`, `charter/genesis.py`, the genesis JSON):

| Quantity | Now | Proposed | Reasoning |
|---|---|---|---|
| `SIM_PER_USD` | 10,000 | **2,000** (20 sim/cent) | a 300-sim daily surplus buys $0.15 = one game-day at the target burn; keeps revive at 10,000 sim = $5 credit (about a game-week at target), which is a real family decision rather than a token gesture |
| burn per agent-day | $2.00 measured | **<= $0.15** | T8/B3 + `keep_doing`; without this no rate works |
| upkeep per head | 90 | **90** (40 food in kind or cash, 50 cash) | already the right order against 200-600 sim/day incomes; food-in-kind makes farming matter |
| head tax | 0 bps | **300 bps = 30/head/day** | ~25% of upkeep; a 5-person household owes 600/day total, which three earners at 300 net clear with 300 to spare; the treasury receives ~600/day from 20 residents to fund W2 |
| starting sim per adult | 200-5,200 (mean 2,345) | **2,000 per adult, 300 per minor, 3,000 per pensioner** | about 4-6 days of the household bill: enough to lose a day, not enough to idle a week |
| business float | 4,000-8,000 | **3 days of payroll + restock (~2,500)** plus posted opening asks at the reference prices | a shop with stock and no price fed nobody in run 23; E1 can measure the first resident-set price instead |
| treasury sim | 0 | **10 days of pensions + payroll (~15,000 for 20 residents)** | W2 needs a float before tax revenue arrives |
| USD wallet | $6 (20-cast) / $2 (250) | **2 game-days of the target burn, i.e. $0.30 at $0.15/day; and the sum of wallets <= 60% of the run's spend cap** | in a $10 run with 20 residents that is $6 of wallets total, so a third of the town can plausibly hit zero and the other two thirds can be tested on whether they revive them |
| revival | 10,000 sim | 10,000 sim (= $5 at the new rate) | unchanged; the assertion in `death.py:1117` becomes `== 500` |
| blessing / wedding / business registration / org | 5,000 / 1,000 / 5,000 / 3,000 | 5,000 / 500 / 3,000 / 1,500 | 2-3 weeks of surplus for a child, a week for a wedding, so they are plans, not impossibilities |

If the operator prefers to keep 10,000:1 (DESIGN-pinned), multiply every sim figure above by 5 (upkeep 450, tax 150, wallets 10,000, bread ~100); the ratios are what matter, not the unit.

---

## 8. Open questions for the operator

1. Should action costs stay in sim (money) as built, or move to USD (energy) as DESIGN §3's two-currency paragraph says? The code and the persona text disagree today; I recommend keeping sim and fixing the text.
2. Is sim supply fixed (closed economy, redistributing treasury) or minted against production (treasury buys grain at a floor)? W2 works either way; the research metrics differ.
3. `SIM_PER_USD`: change the pinned 10,000, or scale the sim price level? (section 7)
4. Upkeep timing: bill at dawn (today's accidental behaviour) or at midnight (the clock's `is_day_boundary`)?
5. Should upkeep's food component be payable in kind by eating (W6), which makes farming load-bearing, or remain pure cash?
6. Household payer for tax: first member, richest earner, or pooled like upkeep?
7. Do temple, pharmacy and college get accounts (fees as income for a keeper/pharmacist/instructor) or keep flowing to the treasury?
8. Minors age to adulthood in 18 game-hours; in a 20-agent town that removes the parent-debit mechanic by mid-morning. Keep the pinned rate, or slow it for small casts?
9. Which root is production after this phase, `run_genesis` or `main.py`? W2's extraction assumes both must run identical day jobs until one is retired.
10. Is a third of the cast reaching $0 in a run acceptable as the stakes calibration (section 7's wallet rule), or should the first full-day runs keep dormancy rare and observe upkeep/tax alone?
