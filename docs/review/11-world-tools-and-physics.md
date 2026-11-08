*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`TOOL-nn`) are referenced from the cross-cutting documents.*

# Review B — the world's physics (tool registry, primitives, rules, budget, perception renderer)

Prefix: **TOOL**. Read-only review of `/home/user/kasukabe` at `Real` @ `5400e94` (2026-09-03).
Scope: `app/world/tools/**`, `app/world/rules/*`, `app/world/call_budget.py`, `app/world/gap_feedback.py`, `app/world/task_label.py`, `app/world/skills_api.py`, `app/world/workshop_api.py`, and the perception renderer (`app/world/retrieval_depth.py`, `app/world/core/perception.py`). Every file in scope was read in full; the runner (`app/world/run_genesis.py`), the ledger schedule (`app/world/ledger/action_costs.py`), the engine seam (`engine/src/sim/world_tools.py`, `pinned_boot.py`, `world_corrections.py`), the charter engines and `docs/runs/run-23-report.md` were read where the physics crosses into them. All paths below are relative to `/home/user/kasukabe/`. "Inferred" marks a claim I could not confirm from a call site.

Test run: `cd app && uv run pytest world/tools -q` → **22 failed, 656 passed, 8 skipped in 3.5s**. 19 of the 22 fail only because the Ville map assets (`smallville/.../the_ville`) are absent in this checkout (movement, place folding, workshop listing); 3 fail because of a real defect (TOOL-09). Detail in §3.

---

## 0. Verdict in one paragraph

The registry is well built as a dispatcher: one path, one lock, snapshot/restore, charge-before-handler with refund, proficiency, intention fulfilment, invocation log, refusal log, event publish (`app/world/tools/registry.py:419-616`). What is not built is a **world for that dispatcher to act on**: place ids have three spellings and no resolver at the tool boundary; production workshops are strings with no ground and no relation to the businesses that supposedly own them; a business's shelf and till are unreachable by its own owner; residents cannot see what they hold; `move` is two mechanisms the resident is never told apart; invented tools are validated only by step name, teleport inside a single call, lose sim on partial failure, and are advertised (name + schema) to every engine regardless of namespace; buildings can never be started (no plot, no licence tool) or finished (no completion caller in the runner); the rulebook that defines supply/demand is read by the viewer and by nothing in the world. None of this needs the engine. All of it is in `app/world/tools/*`, `place_aliases.py`, `run_genesis.py` and a handful of new primitives.

---

## 1. Inventory

Status legend: **LIVE** = on the `run_genesis` production path; **TEST-ONLY**; **ORPHANED** = no production caller found (grep over `app/world`, `app/research`, `app/game` excluding tests).

| Path | Lines | Purpose | Status / proof |
|---|---:|---|---|
| `app/world/tools/__init__.py` | 57 | package facade | LIVE (re-exports `build_world`, `ToolRegistry`, `WorldState`) |
| `app/world/tools/registry.py` | 705 | `ToolSpec`, `ToolRegistry.dispatch/execute`, `tool_registry_rows`, refusal log | LIVE — `run_genesis.py:2824` `registry.execute`; `run_genesis.py:2288` `tool_registry_rows`; `wiring.py:1178` |
| `app/world/tools/bootstrap.py` | 371 | `build_world()`: wires ledger/charter/bus/locations/movement, registers primitives, freezes, registers meta, annotates prices, sets `world.tool_registry` | LIVE — tests' `conftest.py:59`; the runner's `world_state`/`registry` pair can only come from here (inferred; not traced to a line in run_genesis) |
| `app/world/tools/state.py` | 1872 | `WorldState`: owned state, conversations, continuing actions, price board, wages/loans, intentions, licences, open questions, authoring hints, snapshot/restore | LIVE — every handler; `run_genesis.py:4335-4336, 4393, 4614-4686` |
| `app/world/tools/models.py` | 604 | pydantic records (Business, PricePost, WagePromise, LoanRecord, Intention, ToolLicence, ResourceZone, Storage, Plot, Building, CrimeCase, CustomToolDefinition, SkillDefinition, ToolInvocationRecord, ToolRefusalRecord…) | LIVE |
| `app/world/tools/errors.py` | 225 | `ToolError` hierarchy (`context` field for teaching refusals) | LIVE — `run_genesis.py:2826-2832` |
| `app/world/tools/inventory.py` | 75 | item ledger keyed by any owner id (agent, business, storage) | LIVE |
| `app/world/tools/locations.py` | 117 | gate constants: temple, forest, `farmland:<crop>`, `ore_outcrop:<metal>`, workshops `mill/loom_house/sawmill/smithy/carpentry/kitchen` | LIVE |
| `app/world/tools/place_aliases.py` | 193 | `satisfies_gate` (sector↔tool-constant fold, sector∋family, hall↔complex), `known_tool_locations`, `is_real_destination` | LIVE for `satisfies_gate` (`registry.py:416`, everywhere). **`is_real_destination` ORPHANED** — only referenced inside its own file |
| `app/world/tools/recipes.py` | 223 | 28 recipes: 6 mill, 1 loom, 10 smithy, 2 sawmill/carpentry, 8 kitchen | LIVE — `production.py` |
| `app/world/tools/meta.py` | 1427 | `define_tool`/patent trio, `define_business_tool`, `define_organization_tool`, `skillify`, `describe_primitives`, `toolify`, `revise_skill`, `revise_tool`, composite handler | LIVE — `bootstrap.py:321` |
| `app/world/tools/cards.py` | 298 | agent cards, `rank_people` scorer, `TRADE_ALIASES` | LIVE — `primitives/people.py:40` |
| `app/world/tools/cost_disclosure.py` | 630 | price annotation, `quote_action_cost` tool, per-tick economic block | **PARTIAL**: `annotate_tool_description`/`build_action_cost_quote_spec` LIVE (`bootstrap.py:350-357`); `gather_wallet_and_burn`/`estimate_ticks_until_dormancy` LIVE (`run_genesis.py:3523-3532`); **`build_economic_context`/`render_economic_context_block`/`HouseholdUpkeepView` ORPHANED** (only self-references) |
| `app/world/tools/economy_bridge.py` | 166 | `LedgerEconomyPort`, `LedgerGuardianshipPort`, event translators | LIVE — `bootstrap.py:297`, `social.py:32`, `living.py:264`, `run_genesis.py:4988` |
| `app/world/tools/primitives/__init__.py` | 7 | package | LIVE |
| `primitives/movement.py` | 514 | `move` (destination folding, walk vs jump) | LIVE |
| `primitives/places.py` | 376 | `list_places`, `describe_place`, `look_around`, `known_places`, `describe_prices` | LIVE |
| `primitives/people.py` | 139 | `find_people`, `describe_person` | LIVE |
| `primitives/resources.py` | 174 | `harvest`, `mine`, `fell_timber`, `seed_resource_zones`, `regenerate_zones` | LIVE for tools; **`regenerate_zones` called only by `wiring.py:1142` (standalone server) — never by `run_genesis`** |
| `primitives/production.py` | 116 | `craft`, `cook` | LIVE |
| `primitives/commerce.py` | 840 | `post_price`, `withdraw_price`, `buy`, `sell`, `trade`, `transfer`, `store_item`, `withdraw_item` | LIVE |
| `primitives/property.py` | 438 | `transfer_tile`, `auction_land`, `set_will`, `read_will`, `settle_property_estate` | LIVE — estate settlement `run_genesis.py:5104-5108` |
| `primitives/civic.py` | 503 | `buy_energy`, `vote`, `petition`, `register_business`, `register_org`, `file_complaint`, `hire/fire_employee`, `transfer_business` | LIVE |
| `primitives/social.py` | 1040 | `say` (vocative addressing), `start_conversation`, `marry`, `come_of_age`, `pray`, `teach_skill` | LIVE |
| `primitives/crime.py` | 127 | `steal` | LIVE |
| `primitives/justice.py` | 201 | `file/grant/deny_subpoena` | LIVE (government namespace) |
| `primitives/labour.py` | 305 | `set_wage`, `pay_wage`, `check_debts` | LIVE |
| `primitives/living.py` | 548 | `keep_doing`, `revive`, `eat`, `give`, `rest` | LIVE |
| `primitives/institutions.py` | 552 | `set_tax_rate`, `declare_candidacy`, `convene`, `appoint_police`, `arrest`, `rule`, `release` (text provisional) | LIVE |
| `primitives/intent.py` | 162 | `set_intention`, `drop_intention` | LIVE |
| `primitives/construction.py` | 212 | `build_structure`, `complete_construction`, `buildings_due_for_completion` | Registered, but **unreachable in production** (no Plot creator, no licence tool — TOOL-10) and **`complete_construction` has no caller anywhere outside tests** |
| `primitives/conveyance.py` | 387 | `license_tool`, `revoke_license`, `sell_tool`, `teach_tool` | LIVE |
| `app/world/tools/tests/*` (55 files) | ~9,900 | suite | TEST-ONLY |
| `app/world/rules/loader.py` (+`village_rules.json`) | 362 | validated rulebook: wages by trade, market elasticity/spoilage/shortage, staffing targets, emergencies, build queue | **ORPHANED from the world** — readers are `game/static/js/township/build_rules.py:56` and `game/server.py:675` only; nothing under `world/` prices, staffs or spoils off it |
| `app/world/call_budget.py` | 159 | wallet → max model calls per tick; `spent_line`; `local_tool_schemas` knob | LIVE — `run_genesis.py:3560`, `retrieval_depth.py:291`, `run_genesis.py:2287` |
| `app/world/gap_feedback.py` | 160 | rate-limited "things the town could not do" notice | LIVE — `bootstrap.py:346`, `run_genesis.py:4436` |
| `app/world/task_label.py` | 70 | deterministic per-turn task label (spec §4) | **ORPHANED** — `label_for_turn` has no caller in `world/`, `research/` or `game/`; contradicts `docs/STAKES_TIME_COMPOUNDING.md` §4.2 ("joined afterwards by (agent_id, tick)") — nothing does the join |
| `app/world/skills_api.py` | 296 | read-only skill sheets/spread/family tree | LIVE (router mounted in `run_genesis.py` and `main.py`) |
| `app/world/workshop_api.py` | 374 | `/workshop`, `/council` views of invented tools and skills | LIVE (mounted in both hosts) |
| `app/world/retrieval_depth.py` | 1156 | `render_perception_text` and every perception section | LIVE — `run_genesis.py:4342` |
| `app/world/core/perception.py` | 413 | `LocationIndex`, `PerceptionSystem.build_perception` | LIVE — `core/engine.py:150` |

### 1.1 Full tool inventory (76 registered; live dump from `build_world()`)

Columns: namespace · gate (`resolver` = computed from params; `none` = handler checks or no gate) · cost category and base/per-unit/bps from `app/world/ledger/action_costs.py:528-944` · actor requirements · run-23 calls (counted from `**action** \`world:x\`` lines in `docs/runs/run-23-report.md`; the report's transcript is excerpted, so "0" means *no evidence in the excerpt* unless marked ✗ = the report itself lists it under "What nobody reached for", `run-23-report.md:678-680`).

| Tool | ns | gate | cost | base/unit/bps | reqs | run 23 |
|---|---|---|---|---|---|---:|
| say | public | none | social | 1/0/0 | | 224 |
| look_around | public | none | social | 0 | | 217 |
| move | public | none | travel | 1/2/0 | not confined | 196 |
| rest | public | none | social | 1 | | 48 |
| craft | public | resolver (recipe) | labour | 6/8/0 (skill) | | 23 (5 ok / 18 refused) |
| find_people | public | none | social | 1 | | 22 |
| check_debts | public | none | social | 0 | | 20 (all empty) |
| eat | public | none | social | 0 | | 19 |
| list_places | public | none | social | 0 | | 16 |
| describe_primitives | public | none | fee | 1 | | 15 |
| harvest | public | resolver (crop) | labour | 2/3/0 (skill) | | 14 |
| post_price | public | none (handler) | trade | 1 | | 12 |
| trade | public | none (handler) | trade | 2 | | 11 |
| describe_place | public | none | social | 0 | | 11 |
| buy | public | none (handler) | trade | 0/0/100 | | 10 (1 ok) |
| fell_timber | public | forest | labour | 2/4/0 (skill) | | 8 |
| mine | public | resolver (metal) | labour | 3/5/0 (skill) | | 7 |
| give | public | none | social | 0 | | 7 |
| set_intention | public | none | social | 0 | | 5 |
| describe_person | public | none | social | 1 | | 4 |
| transfer | public | none | trade | 0/0/50 | | 3 |
| store_item | public | resolver (existing storage) | trade | 1/1/0 | | 3 |
| sell | public | none (handler) | trade | 0/0/100 | | 3 |
| quote_action_cost | public | none | fee | 0 | | 3 |
| withdraw_price | public | none | trade | 1 | | 2 |
| start_conversation | public | none | social | 2 | | 2 |
| pray | public | temple | social | 3 | | 2 |
| keep_doing | public | none | labour | 0 | | 2 (both refused) |
| cook | public | resolver (recipe) | labour | 5/6/0 (skill) | | 2 (0 ok) |
| withdraw_item | public | resolver (storage) | trade | 1/1/0 | | 1 |
| toolify | public | none | fee | 0 | age≥18 | 1 |
| *(invented)* mill_flour_run | public | none | **DEFAULT_RULE 5** | | owner/licensee | 1 (refused) |
| arrest | public | none (handler) | labour | 2 | police (handler) | ✗ |
| release | public | none | fee | 0 | police/seat (handler) | ✗ |
| drop_intention | public | none | social | 0 | | ✗ |
| set_wage | public | none (handler) | fee | 0 | owner (handler) | ✗ |
| pay_wage | public | none (handler) | fee | 0 | owner/staff (handler) | ✗ |
| revive | public | none (handler) | fee | 0 | | ✗ |
| transfer_tile | public | none | fee | 20 | | ✗ |
| auction_land | public | none | fee | 20 | seat | ✗ |
| set_will | public | none | fee | 15 | | ✗ |
| read_will | public | none | fee | 2 | | ✗ |
| hire_employee | public | none | fee | 10 | | ✗ |
| fire_employee | public | none | fee | 10 | | ✗ |
| transfer_business | public | none | fee | 20 | | ✗ |
| come_of_age | public | none | social | 20 | age≥18 | ✗ |
| teach_skill | public | none (handler) | social | 15 | | ✗ |
| steal | public | none (handler) | labour | 5 | | ✗ |
| build_structure | public | none | labour | 25/15/0 (skill) | builder_license | ✗ (unreachable) |
| license_tool | public | none (handler) | fee | 0 | | ✗ |
| revoke_license | public | none | fee | 0 | | ✗ |
| sell_tool | public | none (handler) | fee | 0 | | ✗ |
| teach_tool | public | none (handler) | fee | 0 | | ✗ |
| define_business_tool | public | none | fee | 0 | age≥18 | ✗ |
| define_organization_tool | public | none | fee | 0 | age≥18 | ✗ |
| skillify | public | none | fee | 0 | age≥18 | ✗ |
| revise_skill | public | none | fee | 0 | age≥18 | ✗ |
| revise_tool | public | none | fee | 0 | age≥18 | ✗ |
| buy_energy | public | government_complex | fee | 0 | | 0 |
| vote | public | town_hall | fee | 2 | age≥18 | 0 |
| register_business | public | government_complex | fee | 0 (+5,000 in handler) | age≥18 | 0 |
| register_org | public | government_complex | fee | 3,000 | age≥18 | 0 |
| file_complaint | public | government_complex | fee | 10 | | 0 |
| declare_candidacy | public | town_hall | fee | 0 | | 0 |
| marry | public | temple | social | 30 (+temple fee) | age≥18 | 0 |
| define_tool | public | patent_office | fee | 0 (+charter fee) | age≥18 | 0 |
| petition | government | government_complex | fee | 100 | seat | 0 |
| set_tax_rate | government | government_complex | fee | 0 | seat | 0 |
| convene | government | town_hall | social | 2 | seat | 0 |
| appoint_police | government | town_hall | fee | 0 | seat | 0 |
| rule | government | town_hall | fee | 0 | seat | 0 |
| file_subpoena | government | government_complex | fee | 25 | seat | 0 |
| grant_subpoena | government | government_complex | fee | 25 | seat | 0 |
| deny_subpoena | government | government_complex | fee | 10 | seat | 0 |
| approve_patent | government | government_complex | fee | 15 | seat | 0 |
| reject_patent | government | government_complex | fee | 10 | seat | 0 |
| revoke_patent | government | government_complex | fee | 20 | seat | 0 |

Totals: 76 tools (65 public, 11 government; 0 business/organization tools exist at genesis). 21 fixed-string gates, 8 resolver gates, 47 with no declared gate (of which ~14 check location in the handler). Cost categories: labour 9, trade 10, social 18, travel 1, fee 38. 31 distinct registered tools appear in the run-23 excerpts; the report's own figure is 41 never-called of 76, so 4 of the 19 gated tools marked "0" above were called at least once (the excerpt does not say which).

---

## 2. How it works today (the live path)

**Boot.** `bootstrap.build_world()` (`bootstrap.py:270-371`): `EventBus`, `LocationIndex.attach(bus)`, `Ledger(event_sink→bus)`, genesis charter, `WorldState`, `seed_resource_zones` (10 strips at `farmland:<crop>`, 5 outcrops at `ore_outcrop:<metal>`, `forest`; yield 10,000, regen 5/tick — `resources.py:33-67`), then the Ville map → `MovementSystem` **and only then** `attach_tile_tracking` + `attach_utterance_log` (`bootstrap.py:310-313`, `267`). 17 primitive modules register (`bootstrap.py:100-131, 316-318`), `freeze_primitives()` (`registry.py:283-290`) fixes the composition whitelist, `meta.build_specs(registry)` adds the 11 authoring tools (`meta.py:551-570`), `quote_action_cost` is registered, and every description gets `annotate_tool_description` appended (`bootstrap.py:350-357`). `world.registry_tool_names` and `world.tool_registry` are set last (`bootstrap.py:328, 369`).

**Genesis placement** (`run_genesis.py:3400-3480`): every resident stands at `home:{household_id}` (or `Kasukabe Market Square` with `--opening gathering`, `run_genesis.py:2929`); the construction company and each small business is a `Business` at `business:{business_id}` (`run_genesis.py:2974, 3449-3467`) with opening stock credited to the **business id** in `world.inventory` (`3474-3477`; genesis_20: b_bakery bread 12 + flour 20, b_mill flour 24 + wheat 30, b_grocery bread 8 + produce). Employees: only `b_yard` has hands (`n03`, `n09`); no wages exist. The standing brief's "workplace" comes from `WORKPLACE_BY_OCCUPATION` (`run_genesis.py:215-235`, `244`: baker → `"kitchen"`).

**Tick** (`core/engine.py:125-175`): clock advances → `movement.advance()` publishes one `agent.moved` per tile crossed (`core/movement.py:335-410`, stride 40 street / 20 off-street tiles per tick) → scheduler picks due agents (cadence 3 ticks, `core/scheduler.py:14-16`) → `PerceptionSystem.build_perception` (`core/perception.py:288-412`). Then `run_genesis.dispatch_due_agents` (`4322-4440`): if the resident is under `keep_doing`, run one repetition via `advance_continuing` (`state.py:1111-1156`) and return unless it stopped; otherwise `note_turn`, render the perception (`retrieval_depth.py:926-1155`: ambient → stop reason → intention → "Said to you" → open questions → "You asked" → standing brief → volatile energy line → silent-turn report → conversation/convening hint → who is here + offers → price board → Recently → notes → playbook → authoring hint → gap notice → the closing law line), compute `call_budget_for` (`run_genesis.py:3552-3568` → `call_budget.budget_for`), and POST `/tick`.

**Engine side** (`engine/src/sim/pinned_boot.py:180-270`): refresh the tool registry (`force=LOCAL_TOOL_SCHEMAS`, so location-reduced rows are re-fetched every tick), then `coordinator.handle_input(perception, max_turns=budget)`. Each `world:<name>` ToolDef POSTs `/agents/{id}/tool_calls {"tool","input"}` (`engine/src/sim/world_tools.py:184-224`).

**Dispatch** (`run_genesis.py:2819-2837` → `registry.execute` `registry.py:584-616` → `dispatch` `419-582`): lock → `snapshot()` → actor exists → tool exists **and visible** (`is_visible` `295-333`: public invented tools only to owner/licensee/taught copies) → actor requirements (`335-381`) → pydantic params (`383-388`) → location gate via `satisfies_gate` (`406-417`) → **charge** `charge_for_action` with proficiency and posted trade value (`481-498`) → handler → on raise: `refund_action` (`506-513`) → practice bump for built-in trades (`525-529`) → intention fulfilled (`538-552`) → skill workflows advanced (`555-558`) → `ToolInvocationRecord` appended (ring 5,000; `569-581`). Back in `execute`: on exception `restore(snapshot)` then `record_refusal` (only for `ToolNotFound`, `LocationGate`, `ActorRequirement`, `ActionUnaffordable` — `655-670`) and re-raise; on success publish every event on the bus (`611-612`). The HTTP layer turns any `ToolError` into 400 `{"error", "context"?}` and any other exception into 400 `{"error"}` (`run_genesis.py:2826-2832`); the engine returns the text `"Error: ..."` to the model and, since T4, writes a `LearningSignal(type="correction", source="engine")` (`world_tools.py:206-219`, `world_corrections.py:60-80`).

**Day boundary** (`run_genesis.py:4614-4686`): health, upkeep (`run_daily_upkeep`, life/upkeep.py), head tax (`charge_the_day_tax` 4688-4750 → `world.arrears`), wage accrual + overdue events (`reconcile_wages` 4752-4795), intentions expire, `assert_the_day_was_charged`. No run has reached one.

**Perception vocabulary**: `Said to you` is built from `heard_events` where `listener_agent_id` is set (`retrieval_depth.py:494-509`); open questions from `state.open_questions` (which reads `addressed_agent_ids`, `state.py:1643-1722`); both read `world.utterances`, which is written only by `attach_utterance_log` (`bootstrap.py:196-236`).

---

## 3. Findings

Format: severity · file:line · evidence · why it matters for the target · fix · effort · layer.

### TOOL-01 — Three spellings of every place, no resolver at the tool boundary (P0, world/registry, M)
- **Where it is minted.** `home:h_x` — `run_genesis.py:2970`. `business:b_x` — `run_genesis.py:2974, 3455, 3467` (every genesis business's premises) and `civic.py:148` (a registered business takes *the caller's current location*). Bare `b_x` — the key of `world.businesses`, what `buy/sell/post_price/set_wage/pay_wage/hire_employee/define_business_tool` expect (`commerce.py:69, 586`, `labour.py:46`, `civic.py:232`, `meta.py:407`). Sector display names (`"Kasukabe Farmland"`, `"Hobbs Cafe"`, `"Kasukabe Government Complex"`) — map sectors (`map/expansion.py:62-66`), what `move` walks to and `list_places` lists (`places.py:138-142`). Tool constants `mill/sawmill/kitchen/smithy/carpentry/loom_house/patent_office/temple/forest/town_hall/government_complex` — `locations.py:62-117`, `charter/models.py:57-59`. Per-variety `farmland:wheat`, `ore_outcrop:copper` — `locations.py:69-84`. Model inventions `loc_*`, `l_*`, `place:t_*`, `h_mill`, `farmland:general` — folded only by `move` (`movement.py:149-355`).
- **How they fold today.** `place_aliases.satisfies_gate` (`99-116`) folds exactly three sector names to three constants (`50-54`), two sectors to their families (`78-81`), and hall↔complex (`86-89`). `canonical_destination` (`movement.py:149-355`) folds *destinations only*, by word-set heuristics, and never a business id form. Nothing folds `business:b_bakery` → `b_bakery` where a business id is a parameter.
- **Evidence (run 23).** `buy(seller_id="business:b_bakery")` → `no seller or buyer with id 'business:b_bakery' - the seller or buyers here are: b_bakery, …` (`run-23-report.md:1641-1642`); `post_price(on_behalf_of_business="business:b_bakery")` by the baker with 17 people waiting → `no business with id 'business:b_bakery'` (`3315-3316`); `post_price(... "business:harvey_oak_supply_store")` ×2 (`1726-1730`). Meanwhile `post_price`'s own success payload advertises `visible_at: "business:b_bakery"` (`commerce.py:669-671`) and the standing brief says "You are at business:b_bakery" — the world teaches the prefixed form and refuses it.
- **Why it matters.** Every commerce, wage and trade-secret verb keys on a business id; every place a resident is told about is a different spelling of the same ground. This is the single largest cause of refusals in the economy.
- **Fix.** One resolver, one canonical scheme: **canonical place id = the map sector name where one exists, else the tool constant; every business gets a canonical `premises` place id; `business:<id>` becomes an alias, never a location.** Concretely: (1) `place_aliases.resolve_business(world, ref) -> Business` accepting `b_x`, `business:b_x`, the display name and a distinctive-word match (reuse `canonical_destination`'s word logic); call it from `commerce._resolve_holder/require_business`, `labour._business_at_hand`, `civic.require_owned_business`, `meta.define_business_tool`. (2) `Business.location_id` must be a real place: genesis assigns premises (bakery → a sector or a stamped building; see TOOL-02), `register_business` takes a `premises` param validated by `is_real_destination` (currently orphaned, `place_aliases.py:159`). (3) `canonical_place()` becomes the *only* comparison key: extend `SECTOR_TO_TOOL_LOCATION`/`ALSO_SATISFIES` from `world.businesses` at call time so a business premises satisfies the workshop gates it provides (TOOL-02). (4) Perception, `look_around.location_id`, `list_places` and tool results all print the canonical id and nothing else.

### TOOL-02 — Workshops are ids without ground; the mill business is not a mill; there is no kitchen (P0, world, M)
- `craft` gates on `recipe.required_location` = `"mill"`, `"sawmill"`, `"smithy"`, `"carpentry"`, `"loom_house"`; `cook` on `"kitchen"` (`recipes.py:86, 97, 106, 123, 141, 173, 199, 206`; `locations.py:96-117`). The comment at `locations.py:87-95` says these are "where a *built* one is expected to register itself" — but nothing registers anything there, `build_structure` is unreachable (TOOL-10), and `known_places` lists them as `kind: workshop` with no map sector (`places.py:150-158`), so `move("mill")` is an opaque jump into a private string (`movement.py:495-497`).
- The genesis mill business `b_mill` lives at `business:b_mill` (`run_genesis.py:3467`). `satisfies_gate("business:b_mill", "mill")` is False. Run 23: `craft: requires location 'mill', actor is at 'business:b_mill'` ×2, `'sawmill'` at `'business:b_mill'` ×2 (`run-23-report.md:669-670` and the error taxonomy) — a miller refused in her own mill.
- **Where would a kitchen be?** Nowhere. The baker's brief says her work is at `"kitchen"` (`run_genesis.py:244`); `Business b_bakery` is at `business:b_bakery`; to bake she must `move("kitchen")` (a jump into an empty string-room no customer can find), `cook`, and jump back. `docs/STAKES_TIME_COMPOUNDING.md` §6 records the scripted control bakery being sited "at the kitchen" for exactly this reason. Bread was baked into Marta's hand in a room with nobody in it; the bakery's 12 opening loaves sat under an id nobody could name.
- **Fix.** Give `Business` a `provides_workshops: set[str]` (mill → {mill}, bakery → {kitchen}, yard → {carpentry, sawmill}); in `satisfies_gate` (or a world-aware wrapper used by `check_location_gate`) treat standing at a business's premises as satisfying the workshops it provides. Keep the bare constants as *town-owned* workshops only where the map has a sector for them; drop the rest from `known_places` so residents stop walking into strings. Add the mapping to genesis JSON and to `register_business` (`business_type` → default workshops).

### TOOL-03 — A business's shelf and till are unreachable by its owner (P0, world/registry, M)
- Inventory is keyed by owner id, and a business's stock is under `business_id` (`inventory.py:27-34`; `run_genesis.py:3474-3477`). Goods produced by `craft/cook` land in the *agent's* hand (`production.py:47-48`). No primitive moves goods **agent → own business** or **business → owner**: `store_item` creates/uses a `Storage` container, not a business (`commerce.py:365-388`); `transfer` is agent→agent only (`commerce.py:306`); `give` is agent→agent; `sell` to your own business needs a posted *bid* and pays you from the till (`commerce.py:209-242`). No primitive moves sim **business → owner** either: `pay_wage` only to `employee_agent_ids` (`labour.py:167`), `transfer` from the actor's own account only.
- The scripted control does it by a private path: `world.inventory.move(self.owner_id, self.business_id, "bread", …)` (`control_bakery.py:116-119`), contradicting its own docstring ("Everything it does goes through `registry.execute`", `control_bakery.py:24-31`) and, for R7, measuring a different world.
- Run 23: `trade: invalid params: you hold 0 bread, not N` ×3, `give … bread` refusals, Marta's loaves in hand while `buy` at `b_bakery` needed the shop's post. The only workaround the physics allows — hire yourself (`civic.py:244-261` has no self-hire check) and `pay_wage` yourself — is a bug, not a feature (TOOL-15).
- **Fix.** Two primitives in `commerce.py`: `stock_shelf(business_id, item, qty)` / `take_from_shelf(...)` (owner or staff, at premises) and `draw_from_till(business_id, amount)` / `deposit_to_till(...)` (owner, at premises; ledger move between agent and business accounts, reason `TRANSFER`). Alternatively generalise `store_item/withdraw_item` to accept a business you own/staff as the container, and add the till pair. Change `control_bakery._bake_when_low` to call the new tool.

### TOOL-04 — Residents cannot see what they hold (P0, world, S)
- The standing brief reduces the inventory to one number, "food units", summing `EDIBLE` (`run_genesis.py:3592-3595`; rendered `retrieval_depth.py:327-348`). `look_around` returns place, people, conversations, prices — not holdings (`places.py:277-335`). No `check_inventory` tool exists; `authoring_context` is the only place `holding` is returned (`state.py:1371, 1394`), and only on authoring calls.
- Run 23 error taxonomy: `eat: 'food' is not food` ×4 (the model ate the word the brief used), `you hold N X, not N` ×13 across `give/trade/eat`, `nN needs N timber, has N` ×2. `check_debts` ×20 was partly residents hunting for *any* read of their own state.
- **Fix.** Add `holding: {item: qty}` (top 8 by quantity, plus "…and N more") to the standing brief (`run_genesis.standing_brief`) and to `look_around`'s result; render as "You hold: wheat 9, bread 2." Free.

### TOOL-05 — `move` is two mechanisms and the resident is never told which; `rest` cancels walks; arrival is not a wake (P1, world, M)
- Walk only when both the destination is a map sector (`world_map.entry_point`, `movement.py:432`) **and** the agent's position resolves to a tile (`resolve_current_tile`, `68-113`); otherwise the pre-walking **opaque jump** (`116-125`, fallback at `497`). Every `home:*`, `business:*`, workshop and resource-family id is therefore a jump; every genesis resident starts at `home:*` with no tile, so the *first* move of a run is always a jump. Run 23: 196 `move` calls, **170** `look_around` results with `"tile": null` versus 3 with a tile; a handful of true walks (`run-23-report.md:2328-2399`).
- The description is "Walk to another named location." (`movement.py:504`); the jump result is `{"location_id": X}` with no `walking` key, so the model reads teleport and walk as the same act. The walk result says "estimated_ticks" but the resident then perceives itself at the *origin sector* for the whole first stride (perception is computed after `movement.advance`, but the first stride only happens on the *next* tick — `engine.py:143-150`; and `location_id_resolver` names the sector of the current tile, `movement.py:387-390`), which is the "look_around during a walk still shows the origin" in the brief.
- `rest` cancels any journey unconditionally (`living.py:199-202`): 12 cancellations in run 23 (`stopped_walking: true` ×12 vs `false` ×36) — residents "resting" mid-walk because the description says "Stay where you are for now".
- Arrival: `agent.moved` carries `agent_id` only (`movement.py:393-402`), not `target_agent_id`/`involved_agent_ids`, so `default_interrupt_policy` (`perception.py:55-69`) never interrupts the scheduler on arrival; the arrival is rendered by the raw fallback as `- [agent.moved, nearby] {...}` (`retrieval_depth.py:841-842`).
- **Fix.** (a) Emit `target_agent_id` on the `arrived=True` event so arrival wakes the resident; (b) render `agent.moved` in `format_heard_event`: "You arrived at X." / "You are walking to X, about N ticks left (now passing Y)."; (c) make jumps cost time too — start a synthetic journey of `estimate_travel_ticks` between the two sectors' entry points, or at minimum return `{"walked": false, "note": "You stepped straight to X; it has no route on the map."}`; (d) `rest(stop_walking: bool = False)` — resting should not silently abandon a journey; (e) seed `agent_tiles` at genesis from each home's entry point so the first move is a walk.

### TOOL-06 — Invented tools: no step-param validation, teleport inside one call, sim lost on partial failure, extra 5-sim fallback fee (P1, registry/world, M)
- `validate_steps` (`meta.py:103-136`) checks only that each step names a frozen primitive and that `$placeholders` exist. Junia's `mill_flour_run` = `withdraw_item → move → craft → move → store_item` (`run-23-report.md:5317`) was accepted with `craft(recipe_id="mill_flour_from_wheat")`, a recipe that does not exist; the first call failed at step 3 (`5362-5363`). **Why:** no step is dry-run against its own `params_model` or the recipe table at filing time. Fix: in `validate_steps`, for each step with fully literal params call `spec.params_model.model_validate` and, for `craft/cook`, `require_recipe`; for placeholder params validate types against `param_schema`.
- `move` inside a composite is dispatched within the same `execute` (`meta.py:167-173`): for a non-sector destination it is an opaque jump, so a five-step tool crosses town twice and mills in **one tick** — the "no teleport" guarantee (`DESIGN.md` §2) is violated by composition. Fix: forbid `move` as a composite step, or make the composite suspend across ticks (a `ContinuingAction` over steps), or at least require that every gate in the composition is satisfiable *where the caller stands*.
- **Money leak.** Each inner `dispatch` charges its step (`registry.py:481-498`); `refund_action` reverses only the step that raised (`506-513`); the outer `execute` restores inventory but the ledger is not snapshotted (`state.py:5-21`). A composite that fails at step 3 has paid for steps 1-2 while the world says they never happened — exactly the case the comment at `registry.py:500-504` says must not occur. Fix: accumulate charges in a per-execute list on `WorldState` (or return them from `dispatch`) and refund all of them in `execute`'s except branch.
- **Fallback fee.** An invented tool's own name has no `SCHEDULE_TABLE` row, so the top-level dispatch charges `DEFAULT_RULE` = 5 sim (`action_costs.py:953-956`, `1050-1051`) on top of every step — contradicting `toolify`'s description "Each step still costs what it always did" (`meta.py:1151`). Fix: `rule_for` returns a 0-cost rule for names in `world.custom_tools`, or `toolify` registers a 0 row.
- **Does an invented tool save a model call?** Yes in dollars for a *successful* composite (five decisions become one turn), no in sim (every step charged). But `keep_doing` cannot repeat an invented tool (`CONTINUABLE_TOOLS` is a fixed set, `state.py:278`), so a composite can never become a "routine that runs with no model call" — the thing `docs/kasukabewhatismissing.md` §3.3 says mastery must mean. Fix: allow `keep_doing` on any tool the caller may call whose steps are all in `CONTINUABLE_TOOLS` ∪ {`move`-free composites}.

### TOOL-07 — Confidential tools are served, by name and schema, to every engine (P1, registry, S)
- `tool_registry_rows` iterates `registry.tools.values()` with no `is_visible` check (`registry.py:191-212`); the embedded route passes it straight through (`run_genesis.py:2277-2289`). Every resident's tool list therefore contains all 11 government tools, every business trade secret's name/description/schema, and every other resident's `toolify` invention (run 23: `registry_version: 77` after `mill_flour_run`, served to all 20). `DESIGN.md` §2: "Non-members cannot see a confidential tool exists" holds only at *call* time (`registry.py:456-460`). `describe_primitives` and `authoring_context` *do* filter (`meta.py:958`, `state.py:1348`) — so the town's own vocabulary listing disagrees with its schema listing.
- Fix: make `tool_registry_rows` async (or pre-compute visibility with the charter state once per call) and drop invisible specs; `main.py:346-363` shares the builder.

### TOOL-08 — Charter engines compare location strings strictly; the registry folds them (P1, world, S)
- `elections.py:195, 230`, `government.py:71-73`, `organizations.py:53-54`, `land_petitions.py:62`, `patent_office.py:62, 90` all `if location != LOCATION_X: raise NotAtRequiredLocation`. The registry gate passes a resident standing at the map's `"Kasukabe Government Complex"` (via `SECTOR_TO_TOOL_LOCATION`, `place_aliases.py:50-54`) and at `"government_complex"` for a town-hall-gated tool (`ALSO_SATISFIES`); the handlers then pass the raw `world.location_of(...)` into the charter (`civic.py:88-96, 116-118, 178-180`; `institutions.py:121-124, 148-154`; `meta.py:212-228, 274-280`). Result: `vote`, `declare_candidacy`, `petition`, `register_org`, `set_tax_rate`, `define_tool`, `approve_patent` refuse residents the registry admitted, with a `CharterError` that is **not** a `ToolError` and so is neither in `tool_refusals` nor in `gap_feedback` (`registry.py:655-670`). This is why the civic surface "has never been called": it cannot succeed from the place `list_places` names.
- Fix: in each handler pass `canonical_place(location)` — or better, pass the gate's own required constant once the registry gate has passed (`spec.location_gate`), since the registry already proved it. Add a test: vote from `"Kasukabe Government Complex"` and from `"Kasukabe Town Square"`.

### TOOL-09 — The utterance log is attached only when the map loads (P1, world, S)
- `attach_utterance_log` is called from `attach_tile_tracking` (`bootstrap.py:267`), which runs only inside `if resolved_map is not None:` (`bootstrap.py:310-313`). Without Ville assets `world.utterances` stays empty, so `open_questions`, `awaiting_reply`, `_continuing_conversation`, the convening hint and the control bakery's "answer whoever asked" are silently dead. In this checkout that is 3 of the 22 test failures (`test_the_asker_learns_they_were_ignored`, `test_group_conversations::test_two_conversations_in_one_room_stay_two`, `test_control_bakery::test_it_always_answers_a_directed_question`). Production has the map, so run 23 was not affected — but a run on a host without the assets would lose every "Said to you" mechanism with no error. Fix: call `attach_utterance_log(world)` unconditionally in `build_world`.

### TOOL-10 — Construction is unreachable and never completes (P0 for the expansion target, world/registry, L)
- `build_structure` requires a `Plot` with `permitted=True` (`construction.py:98-102`) — no tool and no genesis path creates a `tools.models.Plot` (`grep plots[` finds only `map/service.py:354`/`map/parcel.py:186`, which are the *map* package's separate `Plot` class); and `ActorRequirements(required_license_types={"builder_license"})` (`construction.py:210`) — the only issuer is `charter.government.issue_builder_license` (`government.py:285-294`) with **no tool wrapping it**. Therefore `build_structure` cannot be called by anyone in any run, and its `run-23` ✗ is structural.
- Bill of materials: timber = area × 5, metal ore = perimeter × 2, ticks = area × 6 (`construction.py:63-65, 111-115`); note it consumes raw `timber` and `<metal>_ore`, not `planks`/ingots, so the sawmill/smithy chains are irrelevant to building (`recipes.py:187-192` claims planks are "the raw material build_structure consumes" — false).
- Completion: `complete_construction`/`buildings_due_for_completion` (`157-198`) have **no caller** outside tests (grep). Even if a building were started it would stay `UNDER_CONSTRUCTION` forever, and `stamp_building` (the only path by which a building becomes a map sector, `map/service.py:233`) would never run. A built structure therefore never becomes a place with gates.
- Fix (the "town builds its institutions" path): (1) `grant_plot(owner, footprint)` (government, seat, gated complex) writing `world.plots` from the map's parcel registry, and `issue_builder_license(agent)` as a tool over `government.issue_builder_license`; (2) `build_structure` with `purpose ∈ {mill, kitchen, smithy, sawmill, carpentry, loom_house, hospital, police_station, well, road, …}`; (3) call `buildings_due_for_completion` + `complete_construction(map_service=…)` every tick from `dispatch`-adjacent runner code (`run_genesis`), and on completion register the building as a place that `provides_workshops={purpose}` (ties to TOOL-02) and optionally auto-`register_business`; (4) crew: `keep_doing("build", …)` so construction "occupies the crew"; (5) a `tender`/`award_contract` pair in government so the government can pay for it (DESIGN §3/§7).

### TOOL-11 — Newly registered businesses live in the government complex (P1, world, S)
- `register_business` is gated at `LOCATION_GOVERNMENT_COMPLEX` and records `location_id = world.location_of(agent)` (`civic.py:148-155, 455`). Every business registered in a run has the town hall as its premises; `buy/sell/post_price/pay_wage` for it then gate on the complex. Fix: `premises: str` param resolved by `canonical_destination` + `is_real_destination`; default to the owner's home.

### TOOL-12 — Refusal log and gap feedback see under half of the refusals (P2, registry, S)
- `unmet_requirement_for` records only four classes (`registry.py:655-670`). Not recorded: `NotFoundError` (bad business/recipe/storage ids — 20 in run 23), `RecipeNotFoundError` (13), `InsufficientInventoryError`, `ToolValidationError` (holdings/`food`/prefixed names — ~25), ledger `InsufficientFundsError`, every `CharterError` (TOOL-08). The "What the town could not do" table (`run-23-report.md:667-673`) shows 10 refusals for a run with ~70 corrections. Fix: classify `NotFoundError(kind)` as `unknown:<kind>`, `RecipeNotFoundError` as `recipe:<tool>`, `InsufficientInventoryError` as `stock:<item>`, `CharterError` subclasses as `charter:<name>`; keep `ToolValidationError` out except the two vocabulary cases below.

### TOOL-13 — `keep_doing` and `set_intention` refuse the engine's own tool names (P2, world, S)
- The engine presents every tool as `world:<name>`/`world_<name>` (`world_tools.py:47-53, 232`). `canonical_tool_ref` folds that prefix for `skillify` only (`meta.py:619-657`). `keep_doing(tool="world_mine")` → `'world_mine' is not something you can keep doing` (run 23, ×2 — both `keep_doing` calls of the run failed on this); `set_intention(done_when_tool="world_craft")` → refused ×2. Fix: call `canonical_tool_ref(world.tool_registry, …)` in `keep_doing_handler` (`living.py:400`) and `set_intention_handler` (`intent.py:59`).

### TOOL-14 — `craft`/`cook` descriptions name no recipe, `harvest` no crop (P2, registry text, S)
- `production.py:100, 110`: "Convert raw material into a processed good via a recipe." Run 23: 18 of 23 `craft` calls refused, 13 with invented ids (`bread`, `wheat->flour`, `planks`, `oak_planks`, `timber_to_planks`, `mill_flour`, `mill:mill_flour_run`, …), 2 of 2 `cook` refused. The refusal lists the ids (good, `errors.py:165-185`) but only after a paid turn. Fix: enumerate the ids and gates in the description (28 ids ≈ 150 tokens; or a compact `input->output @place` form), and accept `output_item` as an alternative param resolved to the unique recipe.

### TOOL-15 — Owners can hire and pay themselves; hiring needs no consent or presence (P2, world, S)
- `hire_employee` checks only that the employee exists (`civic.py:247-250`), not `!= owner`, not co-location, not consent. `pay_wage` is `owner_only=False` so any employee pays any employee (`labour.py:164-172`). Combined with TOOL-03 this is the only till-withdrawal path and it is accidental. Fix: refuse self-hire; require the employee's consent flag like `teach_skill` (or co-location); make till withdrawal explicit.

### TOOL-16 — Report reads the wrong event type for posted prices (P2, instrumentation, S)
- `post_price` emits `economy.price.posted` (`commerce.py:642`); the run report reads `business.price.posted` (`run-23-report.md:684-686`: "Nobody posted a price … Read from `events` (`business.price.posted`)") while the same document shows 12 `post_price` actions and `holder` results. `earliest_standing_post_tick` in `state.py:1044-1058` warns the report must read the event — which it does, under the wrong name. Fix: one constant for the event type imported by both.

### TOOL-17 — Two definitions of "said to you"; questions outlive the asker's presence (P2, world/perception, S)
- `PerceptionSystem` treats a remark as addressed only via `listener_agent_id` (`perception.py:339-344, 357-362`); `state.open_questions`/`awaiting_reply` use `addressed_agent_ids` (`state.py:1679-1683, 1758`); `format_addressed_to_you` requires `listener_agent_id` (`retrieval_depth.py:499`). A multi-vocative remark ("Anselm… Girl…") reaches the second addressee only in "Recently". `OPEN_QUESTION_TTL_TICKS = 36` (`state.py:140`) and `UNANSWERED_LOOKBACK_TICKS = 30` (`perception.py:232`) expire on time only; neither expires when the asker leaves, hence "unanswered questions persist 30+ ticks after the asker left" (brief). The renderer even says "They are no longer standing with you; `say` only reaches someone in the same place" (`retrieval_depth.py:539-543`) — a claim that is false since `say` no longer refuses absent addressees (`social.py:809-829`).
- Fix: make `addressees_of(payload)` the single predicate in `perception.py` too; drop an open question from the prompt once the asker has been out of the room for a full cadence (keep it in the trace); reword the closing line.

### TOOL-18 — `check_debts` cannot know a genesis debt (P2, world/genesis, S)
- `debts_of` reads wage promises, `loans` (only from `transfer(reason="loan")`) and `arrears` (`state.py:933-993`). The 400-sim debt lives in persona prose only; 20 calls returned empty and the debt was paid twice by two people. Fix: genesis `loans: [{lender, borrower, principal_sim, since}]` seeded into `world.loans`; persona generator writes both sides from one record.

### TOOL-19 — `trade` and `steal` compare places strictly (P2, world, S)
- `commerce.py:494` `here != there`; `crime.py:55, 57`. Every other co-location check uses `satisfies_gate` (`give` `living.py:142`, `revive` `299`, `teach_skill` `social.py:475`, `conveyance.py:73`). Two spellings of one room refuse a trade. Fix: `satisfies_gate` in both.

### TOOL-20 — Zones never regenerate and buildings never complete in the production runner (P2, world, S)
- `regenerate_zones` is called from `wiring.py:1142` (the standalone server nobody runs; every launcher passes `--world-mode embedded`: `start-genesis.sh:43`, `runseries.sh:89`, `run50.sh:83`) and never from `run_genesis`. Same for completion (TOOL-10). Fix: a `world_tick_jobs(world)` function in `tools/` that both hosts call.

### TOOL-21 — The rulebook is not the world's (P2, world, M)
- `rules/village_rules.json` + `loader.py` define wages by trade, upkeep 55/day, head tax base 1,000 at 300 bps, a supply/demand price response (elasticity 0.6, ×0.4–×4.0, 3-day smoothing), 4-day larder with 12%/day spoilage, staffing targets, emergencies, and a government build queue (`guard, lamps, wells, clinic, roads, school2, granary`). Nothing under `world/` reads it. The viewer's browser economy runs on it; the engine economy runs on `SCHEDULE_TABLE` and genesis balances. Two economies, as `HANDOVER.md` §7.3.8 admits. Fix: see §5 (market).

### TOOL-22 — Confinement never ends on its own (P2, world, S — inferred)
- `rule(verdict="jail")` sets `confined[...]["until_tick"]` (`institutions.py:376-382`); I found no tick-loop code reading `until_tick` (grep `confined` in `run_genesis.py` returned nothing). A sentence lasts until somebody calls `release`. Fix: a tick job that pops expired confinements and emits `crime.release.recorded`.

### TOOL-23 — `keep_doing` does not check the underlying gate at commit; repetitions run per cadence, not per tick (P3, world, S)
- Spec B1 says "checked once at commit"; `keep_doing_handler` (`living.py:380-463`) checks the tool name and the stop condition only. A resident commits from the wrong room, is skipped by the scheduler, and is woken three ticks later by the refusal. `advance_continuing` runs once per *due* turn (`run_genesis.py:4335`), i.e. every 3 ticks, so "twenty repetitions" is an afternoon of sixty ticks. Fix: dry-run `check_location_gate` at commit; run repetitions every tick for working residents.

### TOOL-24 — Index rows name the gate in the tools' vocabulary (P3, registry, S)
- `_index_row` says "Only at government_complex; `move` takes you there" (`registry.py:245-249`); `list_places` says "Kasukabe Government Complex". Fix: print `canonical display name` (see TOOL-01).

### TOOL-25 — `is_real_destination`, `task_label.py`, `render_economic_context_block` are dead (P3, world, S)
- See inventory. `task_label` is load-bearing for C1 (`docs/STAKES…` §4.2 claims a join that does not exist).

### TOOL-26 — Descriptions that carry strategy (P3, registry text, S)
- Addendum §0 rule 2 ("never the strategy"): `list_places` "Use this to find where to work, buy, sell, pray, vote, or buy energy credit." (`places.py:343-345`); `describe_primitives` "Use this before composing a tool…" (`meta.py:990-995`); `eat` "The only way to consume food." (`living.py:515-517`); the food line in the brief tells a resident what to do (`retrieval_depth.py:333-348`).

### TOOL-27 — Tests that pass on this checkout only with the map, and one that asserts the wrong thing (P3, tests, S)
- 19 tests fail (not skip) without Ville assets: `test_movement.py` (4), `test_move_resolves_places.py` (3), `test_nobody_stands_in_a_room_they_invented.py` (7), `test_every_gated_tool_is_findable.py` (3), `test_run19_findings.py::test_every_workplace…`, `test_eat_give_rest.py::test_resting_stops_a_walk`; only `test_construction.py:206` skips honestly. CI on a host without the assets reports red for environment, hiding real regressions (TOOL-09 was invisible inside this noise). Fix: a session fixture that skips map-dependent tests with a reason.

### TOOL-28 — `mine costs 0 sim` (brief) is not what the schedule says (open, unverified)
- `mine` = 3 + 5/unit, skill-discounted, min 3 (`action_costs.py:538-541`); Bram at proficiency 34 pays ≈6 per unit call. If the run's ledger showed 0 for `mine`, the charge path was bypassed — the only bypass I can see is `advance_continuing` → `registry.execute` (charges) or a refund after a raise. Needs the run's `transactions` rows (`memo = action_cost:mine`).

### TOOL-29 — Double-charge accounting is correct but the "each step costs what it always did" story is not (P3)
- Handler-side fees (`register_business` 5,000, `define_tool` charter fee, `marry` temple fee) plus schedule rows are documented (`action_costs.py:149-177`) and consistent. The real double charge is TOOL-06's fallback fee, and the lost sim on composite failure.

### TOOL-30 — Organisations have no membership surface (P1, world, S)
- The `organization` namespace is visible to `organization.members` (`registry.py:328-332`); only `register_org` exists (`civic.py:175-192`) and `define_organization_tool` is founder-only (`meta.py:492-495`). No `join_org`/`admit_member`/`leave_org`/`expel` tool: an organisation is its founder forever, so guild/party/temple-keeper mechanics are unreachable by construction (inferred from the tool inventory; `charter/organizations.py` was not read in full).

---

## 4. Gaps vs DESIGN.md (in scope)

| DESIGN §2/§3 promise | State |
|---|---|
| "Every tool is location-gated: at the mill to mill, at the shop to buy" | Gates exist; the mill is not where the mill is (TOOL-02); the shop's id has two spellings (TOOL-01); registered shops are at the town hall (TOOL-11) |
| Primitives `build`, `teach`, `arrest`, `pay/transfer`, `register` | `build` unreachable (TOOL-10); `pay` = `transfer`/`pay_wage` ✓; `arrest` ✓ (needs a case) |
| Freeform building: plot rights + permit, BoM from geometry, crew time ∝ size, unlicensed = crime | BoM ✓ (`construction.py:63-115`); plots/permits/licence have no tools; no crew time (instant debit, timer never fires); unlicensed building is a refusal, not a crime |
| Meta-tool: compositions only, patent office fee/review/revoke, runs under creator | Compositions ✓; patent path ✓; `toolify` bypasses the office (deliberate, `meta.py:1044-1063`); runs under the *caller* (deliberate reversal, `docs/STAKES…` §4.3); depth capped at 1 (ROADMAP T5 asked for 3) |
| Four namespaces, confidential tools invisible | Enforced at call time; **not** at schema serving (TOOL-07); organisation membership has no surface (TOOL-30) |
| Complete causal record; courts subpoena | `tool_invocations` ring of 5,000 in memory (`state.py:119`) — the "record" is the trace store, and refusals are under-recorded (TOOL-12) |
| Agents see energy, money, burn, upkeep, tax, dependents, what an action will cost | Volatile line ✓ (`retrieval_depth.py:231-294`); `quote_action_cost` ✓; dependents/household upkeep block orphaned (TOOL-25); own inventory ✗ (TOOL-04) |
| Prices, wages, contracts emergent | Price board ✓, wages ✓, no contract/tender primitive |
| Businesses registered with fee + taxes; jobs created/cut by government and businesses | Fee ✓; no business tax; no `create_job`/`cut_job`; only `hire_employee` |
| Government powers: taxes, credit-store margin, jobs, permits, pensions, police, tenders, buildings, land petitions, amendment by 3/5 | `set_tax_rate`, `petition`, `appoint_police`, `auction_land` ✓; margin, jobs, permits, pensions, tenders, buildings, `table_amendment` ✗ (`institutions.py:25-28` says amendment is deliberately absent) |
| Elections every 3 game-days, in person | `vote`/`declare_candidacy` exist; charter gate mismatch makes them unreachable from the map's town hall (TOOL-08); no election auto-advance module (`run_genesis.py:113-116`) |
| Theft on unattended inventory | `steal` targets `Storage` only; business shelves and homes cannot be robbed (`crime.py:4-13`) |

---

## 5. Gaps vs the operator's target

**Thriving, not surviving.** Exists: energy loop (`buy_energy`), upkeep/tax/wage day jobs, `keep_doing`, price board, proficiency discount. Missing: any way for a business to function as a business (TOOL-03), a resident to know what it holds (TOOL-04), a mill to mill (TOOL-02), and a day boundary ever reached. Add: TOOL-01…05 first; then a per-tick `work` yield for wage jobs (a hired employee under `keep_doing("work", business)` produces the business's goods into the shelf), so "does its job" is a mechanism rather than a brief line.

**Organisations and government running.** Exists: 11 government tools, subpoena chain, patent office, `convene`, wages. Missing: membership (TOOL-30), business tax and treasury budget, `create_job`/`cut_job` with treasury salaries (police `wage_sim` is recorded and never paid — `institutions.py:227-250` stores no promise), tenders/contracts, `table_amendment`, election phase advance, and a reachable town hall (TOOL-08). Where: new `primitives/government.py` over `charter/*` engines (which already own tax, margin, licences, pensions per `charter/models.py:225`); a `contracts` model in `tools/models.py` (party, counterparty, deliverable, price, due tick, status) with `offer_contract/accept/deliver/settle` — this is also what makes "the town's own builders construct from tenders" expressible.

**Expansion and construction by the town's own builders.** Exists: BoM geometry, `Building`, `stamp_building` in the map service, land petitions and auctions. Missing: everything in TOOL-10. Ordered: licence + plot tools → completion tick job → building becomes a place providing workshops → tender/contract → crew `keep_doing`.

**Skills and tools that cut model calls.** Exists: `keep_doing` (one call per job), `toolify` (one call per composite), authored skills as prompt playbooks, proficiency → sim discount, corrections to the learning centre. Missing: a composite cannot be `keep_doing`-repeated (TOOL-06), composites cannot be validated before they cost a turn, and no invented tool has a cost row. Add: `keep_doing` over composites; step-param validation; a `routine` notion = a validated composite the world may schedule without waking the resident, whose price falls with proficiency — that is the literal "mastery lowers the cost of living" in dollars.

**Supply/demand and ideology-driven mechanics.** Exists on paper: `village_rules.json` (elasticity, spoilage, buffers, shortage thresholds, wages), and in the world: a price board of standing asks/bids with no quantity, no expiry, no stock view, no spoilage, no demand. A market needs: (1) `PricePost.quantity` and stock shown beside the price in `look_around`/`describe_place` (`places.py:201-228` has the hook); (2) a per-place **order book** — the board already is one per holder; add `best_ask(item, place)`/`best_bid` in `state.py` and a `market_report` tool (cheap read: median ask, stock, last 5 settlements from `economy.trade.settled`/`business.sale.completed`); (3) **spoilage** as a tick job over `EDIBLE` in every inventory (rule `market.storage.spoilage_per_day`), which makes farming non-decorative; (4) **demand** = upkeep: `life/upkeep.py` charges sim for "food" without consuming food — make the day job debit `survival.daily_needs.food_units` from the household's inventory or convert to hunger, so bread has a reason to be bought; (5) **ideology hooks**: the charter is already a ruleset of numbers (`charter/models.py:225` lists tax, margin, fees); add `price_ceiling[item]`, `wage_floor`, `rationing[item]`, `property_regime ∈ {private, collective}` as charter fields that `buy/sell/post_price/set_wage/transfer_tile` read at dispatch (a `check_policy(tool, params)` step after the gate) — amendable by the existing 3/5 amendment engine, so an ideology change *is* a charter amendment and the mechanics change on the next call (ROADMAP T6).

**Evolving goals.** Exists: one `Intention` per resident, expiring at day end, cleared by a named tool (`intent.py`, `state.py:822-863`). Missing: horizon, revision by outcome, needs. Add: `Goal(text, horizon ∈ {today, week, life}, set_tick, revised_from, reason)` with up to three live goals; `revise_goal(because=…)` keeping history like `revise_skill`; and world-authored *need* lines (hungry, broke, tax due, dormant kin) rendered as facts so the resident revises. Instrument: goals set/fulfilled/revised per day, and which world event preceded each revision.

**World-class instrumentation.** Exists: `tool_invocations`, `tool_refusals`, events, invented-tool and skill views, control bakery scorecard. Missing/wrong: task label never joined (TOOL-25), refusals under-recorded (TOOL-12), report reads a non-existent event (TOOL-16), invocation ring in memory only, the control bakery bypasses the registry (TOOL-03). Add: `task_label` written at turn close by the runner (it has `tools_called` from the session log), a refusal taxonomy that covers every `ToolError` and `CharterError`, and one event-type constants module shared by handlers and the report.

---

## 6. Recommended work items (P0 first)

1. **TOOL-W1 Canonical place ids and a business resolver** (TOOL-01, 11, 19, 24). Spec: `place_aliases.resolve_business(world, ref)` accepting `b_x`, `business:b_x`, display name, distinctive word; used by every handler that takes a business id; `Business.location_id` is always a canonical map place, set by genesis and by a `premises` param on `register_business`; `trade`/`steal` use `satisfies_gate`; index rows and perception print one spelling. Acceptance: `post_price(on_behalf_of_business="business:b_bakery")`, `"Thorne's Bakery"` and `"b_bakery"` all succeed from the bakery; a business registered at the complex with `premises="Hobbs Cafe"` gates `buy` at Hobbs Cafe; a run-23-style transcript replayed against the resolver produces zero `no business with id` refusals.
2. **TOOL-W2 Workshops belong to businesses** (TOOL-02). Spec: `Business.provides_workshops`; gate check consults it; genesis: mill → mill, bakery → kitchen, yard → carpentry+sawmill; drop groundless workshop ids from `known_places`. Acceptance: the miller crafts `craft:wheat->flour` standing at the mill's premises; the baker cooks at the bakery; `list_places` names no place that is not a sector or a business.
3. **TOOL-W3 Shelf and till** (TOOL-03, 15). Spec: `stock_shelf/take_from_shelf`, `deposit_to_till/draw_from_till`, owner/staff at premises, events `business.stock.moved`, `business.till.moved`; self-hire refused; `control_bakery` uses the tools. Acceptance: bake → stock → post → a stranger buys → owner draws the proceeds, all via `registry.execute`; the control bakery passes with no direct `inventory.move`.
4. **TOOL-W4 Show holdings** (TOOL-04). Acceptance: `eat(item="food")` never occurs in a 20-agent run's transcript; "you hold N X, not M" refusals fall below 2 per run.
5. **TOOL-W5 Construction reachable and completing** (TOOL-10, 20). Spec: `issue_builder_license`, `grant_plot`, tick jobs (`regenerate_zones`, completion), completed building → place providing its purpose's workshop. Acceptance: a seat grants a plot and a licence, a builder with timber+ore calls `build_structure(purpose="mill")`, N ticks later the building is `COMPLETE`, appears in `list_places`, and `craft:wheat->flour` succeeds standing in it.
6. **TOOL-W6 Charter gates fold like the registry** (TOOL-08). Acceptance: `vote`, `declare_candidacy`, `register_org`, `petition`, `set_tax_rate`, `define_tool` succeed from the map's `"Kasukabe Government Complex"`; `CharterError`s are recorded as refusals.
7. **TOOL-W7 Confidential schemas are confidential** (TOOL-07). Acceptance: `GET /tools/registry?agent_id=n05` omits `mill_flour_run` (owned by n13) and every government tool for a non-seat.
8. **TOOL-W8 Composite tools: validate, no teleport, refund all, no fallback fee, repeatable** (TOOL-06). Acceptance: `toolify` with an unknown recipe is refused at filing with the recipe list; a composite containing `move` is refused or suspended across ticks; a composite failing at step 3 leaves the caller's sim exactly as before; an invented tool costs the sum of its steps; `keep_doing(tool="mill_flour_run")` is accepted.
9. **TOOL-W9 Movement honesty** (TOOL-05). Acceptance: every `move` result carries `walked: bool` and `ticks`; arrival wakes the resident with "You arrived at X"; `rest` during a walk keeps walking unless `stop_walking=true`; genesis seeds tiles so the first move walks.
10. **TOOL-W10 Utterance log always attached** (TOOL-09) + map-dependent tests skip (TOOL-27). Acceptance: `uv run pytest world/tools` is green on a host without Ville assets.
11. **TOOL-W11 Refusal taxonomy and event constants** (TOOL-12, 16, 25). Acceptance: every 4xx in a run maps to a `tool_refusals` row; the report's price section reads `economy.price.posted`; `task_label` is present on every session-log turn.
12. **TOOL-W12 Vocabulary folds in `keep_doing`/`set_intention`; recipe lists in descriptions** (TOOL-13, 14).
13. **TOOL-W13 Organisations, jobs, contracts, tenders** (TOOL-30, §5). Acceptance: a founder admits a member who can then call an organisation tool; a seat opens a tender, a builder bids, the contract settles on completion.
14. **TOOL-W14 Market physics** (§5): quantity on posts, stock in `look_around`, spoilage tick, upkeep consumes food, charter policy fields checked at dispatch. Acceptance: bread price at the bakery moves after a spoilage day with no supply; a `price_ceiling` amendment makes `post_price` above it refuse on the next call.
15. **TOOL-W15 Goals** (§5): `Goal` model, `revise_goal`, need lines; acceptance: the goals/revised/fulfilled ratio is in the report.

---

## 7. Open questions for the operator

1. Canonical place scheme: should a business's premises be a map sector (shared with other residents' walks) or a stamped building of its own? The first is cheap now; the second needs TOOL-W5.
2. Should `move` to a non-sector place (home, business) cost ticks by synthetic distance, or should every such place get a real tile so it can be walked? The viewer bridge (`HANDOVER.md` §4) argues for tiles.
3. `toolify` runs under the caller, DESIGN says under the creator. Keep the reversal (needed for licences) and update DESIGN.md?
4. Should invented tools be `keep_doing`-repeatable (a routine with no model call) — the "mastery lowers the cost of living in dollars" claim depends on it?
5. Is the rulebook (`village_rules.json`) meant to become the world's market parameters, or is it the viewer's only? Today it is the latter and `HANDOVER.md` §7.3.8 leaves the decision open.
6. `mine` at 0 sim in run 23 (brief) — I could not reproduce from the schedule; is that figure from `transactions` memos or from the report's cost estimate?
7. Should hiring require the employee's consent (as teaching does)? Today an owner can name anyone.
8. Should `steal` reach business shelves and homes (unattended inventory per DESIGN) rather than only `Storage` containers?
9. Which of the 19 gated tools with no evidence in the excerpt were among the "35 called" in run 23? The report's own 41/76 implies four; the transcript does not say which.
