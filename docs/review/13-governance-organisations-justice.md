*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`GOV-nn`) are referenced from the cross-cutting documents.*

# Review D: institutions (charter, crime, governance, organisations, temple)

Prefix: **GOV**. Read-only review of `Real` @ 5400e94 (2026-09-03). Every claim below is cited `path:line`; where I infer rather than read, it says so. No file in `/home/user/kasukabe` was modified.

`cd app && uv run pytest world/charter world/crime -q -x`: **198 passed in 0.32s**. Both suites are green and both exercise code that has no production caller (see Inventory), so the green is not evidence that the institutions work in a run.

---

## 0. The three sentences that summarise this review

1. **Every institution is built as a standalone engine and none of them is driven by the tick loop.** The first election is scheduled at boot (`run_genesis.py:3485`) and `tally()` has no caller anywhere in `app/` outside tests, so government seats are vacant for the whole run, and every GOVERNMENT-namespace tool (`petition`, `set_tax_rate`, `convene`, `appoint_police`, `rule`, `approve_patent`, subpoenas) is invisible and uncallable by every resident, forever (`registry.py:320-322, 364-366`).
2. **The justice system that is wired is not the one that was built.** `world/crime/` (theft desk, complaints desk, investigation, warrants, arrests, outcomes, false-conviction metric) is constructed only in `wiring.py:1478` for the `world/main.py` host and is never called by any tool; the production host (`run_genesis.py`) never constructs it. The live `steal`/`file_complaint`/`arrest`/`rule`/`release` tools use a parallel three-field `CrimeCase` in `tools/models.py:334-352`, and in that model a theft case is recorded **against the victim** (`primitives/crime.py:80`), so `arrest` and `rule` on a theft case can only ever arrest and fine the person who was robbed.
3. **No policy lever changes any mechanic at dispatch time except the head-tax rate**, which is read once a day (`run_genesis.py:4704-4705`) and is 0 at genesis (`charter/genesis.py:40`), so the only tax the town has ever been able to charge is zero, and the S1 "day was charged" gate (`run_genesis.py:4797-4815`) passes on a zero-sim bill.

---

## 1. Inventory

Status legend: **LIVE** = on the `world.run_genesis` production path (HANDOVER §7 and `run_genesis.py:2186` "The live run boots THIS app, not world/main.py"); **MAIN-ONLY** = constructed only by `world/main.py` via `wiring.py:build_world_server`, which no run has used; **TEST-ONLY/ORPHANED** = no production call site.

### 1.1 `app/world/charter/`

| Path | Lines | Purpose | Status | Evidence |
|---|---|---|---|---|
| `charter/__init__.py` | 52 | package docstring: politics as data | LIVE | imported via `charter/engine.py` from `tools/bootstrap.py:44,298` |
| `charter/models.py` | 624 | `Charter`, `GovernmentPolicy`, `GovernmentState`, `Election`, `AmendmentProposal`, `Job`, `License`, `Tender`, `PatentApplication`, `Patent`, `LandPetition`, `Organization`, `ComplaintEntry` | LIVE | used by every sub-engine, `registry.py`, `governance_api.py` |
| `charter/engine.py` | 60 | `CharterEngine` facade + `create_genesis_charter_engine` | LIVE | `tools/bootstrap.py:298` |
| `charter/genesis.py` | 48 | seeds `Charter(version=0)` and `GovernmentPolicy(version=0)` (tax 0, margin 0) with vacant seats | LIVE | called from `engine.py:58` |
| `charter/store.py` | 362 | lock-guarded in-memory store, `snapshot()/restore()` with self-check | LIVE | checkpointed by `run_genesis.py:1710-1721,3118` |
| `charter/events.py` | 93 | event catalogue + `EventSink` seam | LIVE but **sink is `noop_sink` in production**: `rewire_charter_event_sink` is called only at `wiring.py:1443`; `run_genesis.py` never rewires it (grep: no `rewire_charter_event_sink`/`noop_sink` in run_genesis except `head_tax` `event_sink=None` at 4730) |
| `charter/errors.py` | 210 | typed `CharterError` catalogue | LIVE | raised through handlers; `registry.py:15-30` documents pass-through |
| `charter/elections.py` | 328 | schedule, advance, declare, vote, tally, transfer of power | **PARTIAL**: `schedule_election` LIVE (`run_genesis.py:3485`); `declare_candidacy` LIVE via `institutions.py:151`; `cast_vote` LIVE via `civic.py:87`; **`tally()` ORPHANED** (grep `\.tally(` finds nothing outside charter tests); `advance_phase` only via the lazy catch-up inside declare/cast |
| `charter/amendments.py` | 277 | propose / vote / sign / veto; `proposals` snapshot | **PARTIAL**: `propose_amendment` LIVE only through `skillify` governmental workflows (`meta.py:838`); **`cast_amendment_vote` has NO caller anywhere** (grep GOVTOOLS: only `governance_api.py` comments mention it); `sign_amendment`/`veto_amendment` via operator routes `admin_api.py:692-710` and `wiring.py:1253-1257` |
| `charter/government.py` | 385 | tax, margin, pensions, jobs, police hire, licences, tenders | **PARTIAL**: `set_tax_rate` LIVE via `institutions.py:123`; `set_credit_store_margin`, `set_pension_config`, `create_job`, `cut_job`, `hire_police`, `issue_license`/`issue_permit`/`issue_builder_license`, `revoke_license`, `post_tender`, `award_tender`, `cancel_tender` are **TEST-ONLY** (grep across `app/world/tools` and `app/world/*.py`: zero callers) |
| `charter/organizations.py` | 116 | register, add/remove member, list by kind | **PARTIAL**: `register_organization` LIVE via `civic.py:174`; **`add_member`/`remove_member`/`list_by_kind` ORPHANED** (no tool, no route) |
| `charter/land_petitions.py` | 135 | file / list_pending / approve / deny | LIVE: `file_petition` via `civic.py:110`; approve/deny via `admin_api.py:631-680`, `wiring.py:1246-1251` |
| `charter/patent_office.py` | 168 | file / approve / reject / revoke | LIVE via `meta.py:225,279,337,369` |
| `charter/complaints.py` | 64 | immutable constitutional complaint ledger | **ORPHANED write side**: `ComplaintLedger.file_complaint` has no caller (the `file_complaint` tool writes `tools/models.CrimeCase`, `civic.py:195-227`); read side served by `/governance/complaints` (`governance_api.py:777-794`) over an always-empty list |

### 1.2 `app/world/crime/`

| Path | Lines | Purpose | Status |
|---|---|---|---|
| `crime/__init__.py` | 58 | package docstring | MAIN-ONLY |
| `crime/models.py` | 337 | `WitnessEvent` (ground truth), `ComplaintRecord`, `CaseFile`, `InterviewLog`, `Warrant`, `ArrestRecord`, `CaseOutcome`, `ConfinementRecord`, `FalseConvictionStats` | MAIN-ONLY (types also imported by `ledger/head_tax.py:88` for `file_head_tax_delinquency`, itself uncalled) |
| `crime/engine.py` | 72 | `CrimeEngine` facade, `compute_metrics` | MAIN-ONLY: constructed at `wiring.py:1478-1484`; only method reached is `compute_metrics` via `WorldServer.crime_metrics` (`wiring.py:1396-1398`) over an empty store |
| `crime/ports.py` | 102 | `EconomyPort`, `HealthPort`, `GovernmentPort`, `LocationPort`, `require_co_located` | MAIN-ONLY; adapters at `wiring.py:193-282` |
| `crime/store.py` | 178 | `CrimeStore` incl. `case_ground_truth_links` | MAIN-ONLY; **no `snapshot()/restore()`**, never checkpointed |
| `crime/theft.py` | 123 | `TheftDesk.attempt_theft` (70/30 rolls, ground-truth record) | **ORPHANED**: `steal` tool (`primitives/crime.py`) never calls it |
| `crime/fights.py` | 137 | `FightRing.resolve_fight` (injury only, never death) | **ORPHANED**: no `fight` tool exists |
| `crime/desk.py` | 144 | `ComplaintDesk.file_complaint` (opens/attaches `CaseFile`, links ground truth) | **ORPHANED**: `file_complaint` tool bypasses it |
| `crime/investigation.py` | 137 | `log_interview`, `read_case_evidence` (the "not omniscient" boundary) | **ORPHANED**: no `interview`/`read_case` tool |
| `crime/warrants.py` | 161 | `request_warrant` (police), `sign_warrant`/`deny_warrant` (minister) | **ORPHANED**: no tool |
| `crime/arrests.py` | 97 | `ArrestDesk.arrest` | **ORPHANED**: `arrest` tool (`institutions.py:263`) uses `world.confined` |
| `crime/outcomes.py` | 297 | `impose_fine`, `impose_jail`, `dismiss_case`, `release_due_confinements`, `tools_locked_to` | **ORPHANED**: `rule`/`release` tools use `world.confined`; no tick sweep calls `release_due_confinements` (grep: only its own docstring) |
| `crime/metrics.py` | 79 | `compute_false_conviction_stats` | MAIN-ONLY via `compute_metrics`; the live path has no ground-truth link so it cannot compute anything real |
| `crime/events.py` | 56 | `crime.*` catalogue | MAIN-ONLY |
| `crime/errors.py` | 117 | typed errors | MAIN-ONLY (`admin_api.py:78,568` installs a handler) |

### 1.3 APIs

| Path | Lines | Purpose | Status |
|---|---|---|---|
| `world/governance_api.py` | 870 | read-only charter/policy/jobs/licences/tenders/complaints/elections/treasury/terms | LIVE: mounted `run_genesis.py:2211` and `main.py:198`; consumed by console `game/console/world_client.py:150-153` (`/governance/policy-and-treasury`, `/governance/terms`) |
| `world/social_api.py` | 523 | scenario feed + conversations over trace `events`/`utterances` | **MAIN-ONLY**: mounted at `main.py:199` only; `run_genesis.py:2186-2226` mounts agents/location/governance/family/skills/workshop, not social |
| `world/family_api.py` | 432 | survival/household/wealth/tree/money-flow views | LIVE: `run_genesis.py:2216`, `main.py:203` |

### 1.4 Tool primitives in scope (all registered by `tools/bootstrap.py:56-65,101-130`, so LIVE as *surfaces*)

| Module | Tools | Notes |
|---|---|---|
| `primitives/civic.py` (503) | `buy_energy`, `vote`, `petition`, `register_business`, `register_org`, `file_complaint`, `hire_employee`, `fire_employee`, `transfer_business` | `petition` is GOVERNMENT namespace + seat (`civic.py:454-461`) |
| `primitives/institutions.py` (552) | `set_tax_rate`, `declare_candidacy`, `convene`, `appoint_police`, `arrest`, `rule`, `release` | Part D; docstring says text is provisional and `table_amendment` deliberately absent (`institutions.py:1-30`) |
| `primitives/justice.py` (201) | `file_subpoena`, `grant_subpoena`, `deny_subpoena` | GOVERNMENT namespace; reveal returns `world.tool_invocations` in result only |
| `primitives/crime.py` (127) | `steal` | Storage-only, owner-absent, two rolls, `CrimeCase` |
| `primitives/social.py` (1040) | `pray`, `marry` (temple) | `pray` donation goes to treasury (`social.py:420-431`) |
| `primitives/construction.py` (212) | `build_structure` | requires `plot.permitted` (`:101`) and `builder_license` (`:210`) that nothing can issue |

**Ever called live** (grep over `docs/runs/*.md` action lines): `world:pray` 10, `world:register_business` 1, `world:define_tool` 1. Zero `vote`, `declare_candidacy`, `petition`, `register_org`, `file_complaint`, `set_tax_rate`, `convene`, `appoint_police`, `arrest`, `rule`, `release`, `steal`, subpoena. Run 23 §"Still zero" (`run-23-report.md:66`) and the never-called list (`:680`) agree.

### 1.5 Rules file

`world/rules/village_rules.json` (433) + `rules/loader.py`: consumed **only** by `game/server.py` (`/api/rules`), `game/static/js/township/rules.js`, `build_rules.py` and the browser sim `township-v2.js`. No module under `app/world/` other than `rules/` imports it (grep). Its `economy.tax_rate_bps: 300`, `government.salaries_per_day_sim`, `government.term_days: 90`, `government.treasury.reserve_floor_sim`, `government.documents`, `unrest` are therefore a **second rulebook the world server never reads**. Status: LIVE for the browser diorama, ORPHANED for the world.

### 1.6 Wiring in the two hosts

| Concern | `run_genesis.py` (production) | `wiring.py`/`main.py` (never run) |
|---|---|---|
| Charter engine | via `build_world` (`bootstrap.py:298`) | same |
| Charter event sink | **noop** (never rewired) | rewired to bus `wiring.py:1443` |
| Charter checkpoint | `CharterCheckpointAdapter` `run_genesis.py:1672-1721,3118` | **not registered** (`wiring.py:1459-1461` registers only `world_state`, `map`); `WorldState.snapshot` excludes `charter` (`state.py:327-361`) so a main.py resume loses all governance |
| CrimeEngine | **not constructed** | `wiring.py:1478-1484` |
| Head-tax debt store | `run_genesis.py:3014`, **not checkpointed** (grep `checkpoints.register`: no head_tax) | absent |
| First election | `schedule_election(0)` at `run_genesis.py:3485` | none |
| Map for petitions | `AdminDeps(map_service=None)` `run_genesis.py:2178` | real `MapService` |

---

## 2. How it works today (the live path)

### 2.1 Boot
`GenesisRunner.boot` -> `build_world()` (`tools/bootstrap.py:278-302`) constructs `Ledger`, `create_genesis_charter_engine(genesis_tick)` with **no sink** (`bootstrap.py:298`), the registry with `civic`, `institutions`, `crime`, `justice` primitives registered (`bootstrap.py:56-65, 101-130`). `seed_genesis` (`charter/genesis.py:27-48`) pushes `Charter(version=0)` (3-day interval, 1 PM + 4 ministers, 3/5 threshold, suffrage 18, fees 1000/200/500, `standing_orders=()`) and `GovernmentPolicy(version=0)` (tax 0 bps, margin 0, pensions 25/50 @70). Seats vacant (`GovernmentState()` defaults, `models.py:239-255`). Then `run_genesis.py:3485` `schedule_election(0)`: candidacy 0-143, campaign 144-287, voting 288-431 (`elections.py:129-160`, `days_to_ticks` at `models.py:52-58`). The `politics.election.scheduled` event goes to `noop_sink`, so no resident and no trace row learns an election exists. Checkpoint adapters registered at `run_genesis.py:3106-3126` (ledger, world_state, locations, counters, charter, records).

### 2.2 A resident and the civic tools
Every call goes through `ToolRegistry.execute` (`registry.py:584`): visibility (`is_visible` `registry.py:295-333`: GOVERNMENT namespace visible only to `is_sitting`), actor requirements (`check_actor_requirements` `registry.py:335-380`: alive, not dormant, age, `require_not_confined` for `move`, `require_government_seat`, licences), params, location gate (folded through `place_aliases.satisfies_gate` `place_aliases.py:83-105`, so government complex == town hall on this map), handler, events. The vocabulary shown on a refusal (`state.py:1330-1370`) also filters by `is_visible`, so a non-seat resident never sees `petition` or `set_tax_rate` exist.

- `vote` (`civic.py:82-99`): needs `election_id` + candidate; `cast_vote` (`elections.py:226-263`) requires town hall, phase VOTING_OPEN (tick >= 288), suffrage, one ballot. Costs 2 sim (`action_costs.py:802`). Registry also hard-codes `min_age=18` (`civic.py:61, ~449`).
- `declare_candidacy` (`institutions.py:139-176`): town hall, CANDIDACY_OPEN (ticks 0-143); emits `politics.candidacy.declared` with `involved_agent_ids = sorted(world.agents)` (wakes everyone). Requires an `election_id` that no tool exposes (grep: `election_id` appears in no primitive but `vote`/`declare_candidacy`; `/world/state` at `location_api.py:658-686` is an HTTP view, not a tool).
- `tally` is never called, so no phase ever reaches TALLIED, seats stay vacant, no next election is scheduled.
- `petition` (`civic.py:102-125`): GOVERNMENT namespace, seat required, 100 sim (`action_costs.py:806`); `file_petition` (`land_petitions.py:47-82`) re-checks seat + complex. Operator lists at `GET /admin/president/petitions` (`admin_api.py:624-629`), approves at `POST .../approve` (`:631-667`) with `edge/depth/new_sector_name/arena_names`; with `map_service=None` (`run_genesis.py:2178`) it logs "decision recorded, no physical map growth applied" (`admin_api.py:658-664`).
- `set_tax_rate` (`institutions.py:112-134`) -> `GovernmentActions.set_tax_rate` (`government.py:83-100`) pushes a new `GovernmentPolicy` version; read by `charge_the_day_tax` at the next day boundary (`run_genesis.py:4704-4705`).
- `register_org` (`civic.py:163-192`): 3000 sim action cost (`action_costs.py:822`), complex gate, `OrganizationRegistry.register_organization` (`organizations.py:44-83`) -> `Organization{members:{founder:"founder"}}`. Nothing else can ever change `members`.
- `file_complaint` (`civic.py:195-227`): appends `CrimeCase(kind=COMPLAINT, filer=agent, against=named)` to `world.crime_cases`; 10 sim.
- `steal` (`primitives/crime.py:46-113`): needs a `Storage` (created by `store_item`, `commerce.py:375`) at the thief's location whose owner is absent; rolls 70/30; **appends `CrimeCase(kind=THEFT, filer_or_actor=thief, against_agent_id=storage.owner_id)`** (`:73-80`); payload of `crime.theft.attempted` carries `thief_agent_id` (`:96-97`).
- `appoint_police` (`institutions.py:235-260`): seat + town hall; `world.police.add(agent_id)`; `wage_sim` is recorded in the event only.
- `arrest` (`:263-337`): caller in `world.police`; case must exist and `case.against_agent_id == target` (`:285-291`); co-located; writes `world.confined[target]` and emits `agent.moved` to `LOCATION_JAIL = "town_hall"` (`:54`). `move` is refused while confined (`registry.py:355-361`, `movement.py:512`).
- `rule` (`:340-420`): seat + town hall; `fine` moves sim accused -> treasury with `TransactionReason.TAX` (`:373-381`); `jail` sets `until_tick` (`:386-389`) but **nothing ever reads `until_tick` to release** (grep: only the refusal message at `registry.py:358`); `dismiss` frees.
- `release` (`:423-445`): police or seat; pops `world.confined`.
- Subpoenas (`justice.py`): GOVERNMENT namespace, separation of filer/decider (`:84-97`), reveal in `result` only.
- `pray` (`social.py:416-450`): optional donation agent -> **treasury** (`TRANSFER` reason), appends `PrayerRequest`; operator answers via `/admin/god/prayers/{id}/grant|silent-deny` (`admin_api.py:792-845`; grant mints sim, `wiring.py:1262-1270`).
- `marry` (`social.py:328-345`): temple gate, 1000 sim `TEMPLE_WEDDING_FEE_SIM` (`life/constants.py:112`, `household.py:111`).

### 2.3 The day boundary
`run_genesis.py:4610-4686`: health, upkeep (`:4660`), `charge_the_day_tax` (`:4674`), `reconcile_wages` (`:4675`), intentions expire, `assert_the_day_was_charged` (`:4685`). `charge_the_day_tax` (`:4688-4750`): reads `policy.tax_rate_bps` (0), groups alive agents by household, **payer = `heads[0]`** (`:4719`), calls `charge_daily_head_tax` (`head_tax.py:365-455`: `sim_per_head = 1000 * bps // 10000`, one `ledger.move(TAX)` per head, shortfall -> `HeadTaxDebtStore.accrue`) with `event_sink=None` (`:4730`), copies debts into `world.arrears` (`:4738-4739`). No pensions, no salaries, no tender payments, no police pay happen at any boundary (grep: `pay_pension` defined at `ledger/core.py:689` with no caller; `Job.salary_sim` read only by `governance_api.py:168,227`).

### 2.4 Checkpoint
`CharterCheckpointAdapter` snapshots `store.snapshot()` (`store.py:311-336`: all 12 owned collections) plus `amendments.snapshot()` (`amendments.py:104-110`); restore overwrites the fresh genesis seed inside `restore_from_checkpoint`. `police`/`confined`/`crime_cases`/`subpoenas`/`prayer_requests` ride on `OWNED_STATE_FIELDS` (`state.py:215-266`). Not covered: `HeadTaxDebtStore` (`run_genesis.py:3014`; its own `snapshot/restore` at `head_tax.py:258-266` is never registered), and `CrimeStore` (no snapshot method at all).

---

## 3. Findings

Severity: P0 blocks the target / corrupts data or money; P1 major gap; P2 defect; P3 polish. Layer: seam / world / registry / frontend / broker / engine-finding.

**GOV-01 (P0, world) The election never concludes; government is vacant for the whole run.**
`elections.py:265-328` `tally()` has no production caller (grep `\.tally(` in `app/world`, `app/game`, `engine/src`: only charter tests). `run_genesis.py:105-117` admits "Full election phase-machine auto-advancement ... still belongs to a future charter-tick module". `GovernmentState` therefore stays `pm_agent_id=None` (`charter/genesis.py:14-18`). Consequence: `registry.py:320-322` hides and `:364-366` refuses every GOVERNMENT-namespace tool for every resident, so `petition`, `set_tax_rate`, `convene`, `appoint_police`, `rule`, `approve_patent`, `reject_patent`, `revoke_patent`, `file_subpoena`, `grant/deny_subpoena` have been uncallable in all 23 runs by construction, not by apathy. `declare_candidacy`'s docstring ("every ballot was empty by construction", `institutions.py:143-147`) fixed half the problem; the other half is this. Also no second election is ever scheduled (only `run_genesis.py:3485`), so DESIGN §7 "every 3 game-days" is unimplemented. **Fix:** a `charter_tick(timestamp)` day-boundary job in `GenesisRunner` (beside `charge_the_day_tax`) that (a) `advance_phase` on the open election every tick or at each phase boundary tick, (b) `tally()` at `voting_closes_tick`, (c) `schedule_election(tallied_tick + interval - windows)` so the next cycle exists, (d) publishes `politics.election.*` on the bus (needs GOV-04). Effort S. Acceptance: a 3-game-day mock run ends with `government_state.pm_agent_id` set and a second election in `elections`; `GET /voting/elections` shows two rows.

**GOV-02 (P0, world) A theft case is filed against the victim, so the justice tools convict the robbed.**
`primitives/crime.py:73-80`: `CrimeCase(filer_or_actor_agent_id=agent.agent_id /*thief*/, against_agent_id=storage.owner_id /*victim*/)`. `arrest` requires `case.against_agent_id == params.agent_id` (`institutions.py:285-291`) and `rule` fines `case.against_agent_id` (`:373-381`). The `arrest` description promises "`steal` opens one by itself" (`:498-503`), i.e. a case against the thief. Net: the only person a police officer can arrest on a theft case is the victim; `rule fine` takes the victim's money. This also means no `CrimeCase` ever names a *suspect* distinct from ground truth, so the false-conviction rate (DESIGN §6, ROADMAP Q20) is unmeasurable on the live path. **Fix (world):** give `tools/models.CrimeCase` explicit `victim_agent_id`, `accused_agent_id: str | None`, `ground_truth_perpetrator_id: str | None` (write-once, never returned by tools), have `steal` set victim=owner, accused=None, ground_truth=thief; have `file_complaint` accept `accused_agent_id` and optional `case_id` to attach to an existing theft case; `arrest`/`rule` act on `accused_agent_id`; add `metrics` that compare `rule` verdicts against `ground_truth_perpetrator_id` (port `crime/metrics.py:37-87`). Effort M. Acceptance: test where A steals from B, B complains naming C, seat rules "fine" -> false-conviction count 1; complaint naming A -> true-conviction 1.

**GOV-03 (P0, world+registry) No amendment can ever pass: there is no vote tool and no table tool.**
`amendments.py:166-208` `cast_amendment_vote` has zero callers (grep). The only proposer is `skillify` with `governmental=True` (`meta.py:833-846`), which files a `standing_orders` amendment that then sits PROPOSED forever; `table_amendment` is "deliberately absent" (`institutions.py:26-30`). DESIGN §7's "charter amendment by 3/5 vote, including election rules and collapse to one-person rule" and the `models.py:20-27` claim that one-person rule is reachable are therefore false at runtime. **Fix (registry):** two GOVERNMENT tools in `institutions.py`: `table_amendment(field_name, new_value, title, rationale)` -> `propose_amendment`; `vote_amendment(proposal_id, support)` -> `cast_amendment_vote`; plus a perception line "amendments awaiting your vote" for seat holders; operator sign/veto stays (GOV-15 discusses auto-sign). Effort S. Acceptance: 5-seat mock government tables `election_interval_days=6`, three yes votes -> `PASSED_AWAITING_PRESIDENT`; sign -> `charter.version==1` and next `schedule_election` uses the new interval.

**GOV-04 (P1, world) Charter engine events are lost in production; duplicated in the other host.**
`run_genesis.py` never calls `rewire_charter_event_sink` (`wiring.py:284-306`), so every event emitted inside charter sub-engines (`politics.election.scheduled/phase_advanced/tallied`, `politics.regime.changed`, `politics.charter.*`, `politics.patent.*` from the office, `politics.petition.approved/denied`, `politics.organization.member_*`, `politics.complaint.filed`) goes to `noop_sink`. Tool handlers re-emit their own copies for the actions they wrap (`civic.py:90-97,111-121,181-190`; `institutions.py:126-133,161-175`), so in `main.py` those are **double** rows and in `run_genesis` the engine-only ones (tally, regime change, approval, phase advance) are **absent**. `social_api.EVENT_TYPE_TO_CATEGORY` (`:98-140`) maps exactly the engine-only names for its REGIME/ELECTION/AMENDMENT categories, so the scenario feed would be empty for politics even if it were mounted. **Fix (world):** call `rewire_charter_event_sink(world_state.charter, ...)` in `GenesisRunner.boot` right after `build_world`, and delete the handler-side duplicates (or make handlers not emit what the engine emits). Effort S. Acceptance: one `politics.tax_rate.set` row per `set_tax_rate` call in the trace on both hosts; `politics.election.scheduled` present at tick 0.

**GOV-05 (P1, world) `world/crime/` is a second, unwired justice system; three definitions of "police", three of "complaint".**
Police: `world.police` set (`institutions.py:239`), charter `Job(job_type="police")` via `hire_police` (`government.py:209-238`, no tool), and `CharterGovernmentAdapter.is_police_officer` reading those Jobs (`wiring.py:274-282`) which `appoint_police` never writes. Complaints: `charter/complaints.py` (constitutional ledger, unused), `crime/desk.py` (unused), `tools/models.CrimeCase` COMPLAINT (live). Cases: `crime/models.CaseFile` (unused) vs `CrimeCase` (live). `ledger/head_tax.py:566-600` `file_head_tax_delinquency` targets the unused `crime.desk` and is itself uncalled. DESIGN §6's investigation, interview, warrant, minister signature, "not omniscient" evidence view and `release_due_confinements` exist only in the orphaned package. **Fix (world, choose one):** (a) construct `CrimeEngine` in `GenesisRunner.boot` with the `wiring.py` adapters, route `steal`/`file_complaint`/`arrest`/`rule`/`release` through it, add `interview`, `read_case`, `request_warrant`, `sign_warrant` tools, register a `CrimeStore` checkpoint adapter, and add the `release_due_confinements` sweep to the tick; or (b) delete `world/crime/` and grow `tools/models.CrimeCase` per GOV-02. (a) preserves the epistemic design the research needs. Effort L. Acceptance: ROADMAP R3 gate "a complaint pursued through investigation and warrant to a verdict, with ground truth recorded alongside" runs end-to-end in a mock run and `crime_metrics` returns non-zero totals.

**GOV-06 (P1, world) The only tax the town can charge is zero, and the S1 gate cannot fail.**
`charter/genesis.py:40` seeds `tax_rate_bps=0`; `set_tax_rate` is uncallable (GOV-01); so `head_tax_sim_per_head(0)==0` (`head_tax.py:162-171`) and every "charged" household moves 0 sim. `assert_the_day_was_charged` (`run_genesis.py:4797-4815`) counts calls, not sim, so it passes vacuously; the evaluation-spec S1 claim "head tax charged at every day boundary" is satisfied by a zero bill. `village_rules.json` says `tax_rate_bps: 300` but nothing in the world reads it (Inventory §1.5). **Fix:** seed a non-zero genesis rate from the genesis file (a `policy` block in `genesis_*.json`, read in `seed_genesis`), make the S1 gate assert `sum(paid)+sum(new_debt) == heads*sim_per_head` when `sim_per_head>0`, and log the rate in the day record. Effort S. Acceptance: mock run crossing day 1 shows treasury sim increased by `heads*sim_per_head`.

**GOV-07 (P1, world) Nothing can ever be built: permits and builder licences have no issuing tool.**
`construction.py:101` refuses without `plot.permitted`; `:210` requires `builder_license`; the only writers are `government.issue_permit/issue_builder_license` (`government.py:273-311`) with no tool and no route. The target's "town expands on its own; its builders construct" is blocked at the first gate. **Fix (registry):** `issue_permit(plot_id, holder)` and `issue_builder_license(holder)` GOVERNMENT tools charging `charter.permit_fee_sim`/`builder_license_fee_sim` to the treasury via the ledger (the engine only records `fee_paid_sim`, it never debits, `government.py:242-271`); mark `plot.permitted`. Effort S. Acceptance: seat issues licence + permit; `build_structure` succeeds in a mock run. (Map/materials side: growth reviewer.)

**GOV-08 (P1, world) The treasury has revenue but no spending; government jobs, police salaries, pensions and tenders move no money.**
Revenue that flows: head tax (0 today), `BUSINESS_REGISTRATION_FEE_SIM=5000` (`civic.py:59,135`), `rule` fines, `pray` donations, wedding fee, `buy_energy` sim (treasury USD out). Spending: none automated. `Job.salary_sim` is never paid (grep); `appoint_police.wage_sim` is event-only (`institutions.py:246-259`); `Ledger.pay_pension` (`ledger/core.py:689-717`) has no caller and ignores `GovernmentPolicy.pension_*` (uses module constants); `award_tender` (`government.py:352-378`) moves nothing. `treasury_view` (`governance_api.py:573-607`) already breaks out wages/pensions it will never see. **Fix (world):** a `run_treasury_day(timestamp)` day job: pay each FILLED `Job.salary_sim` and each `world.police` wage from treasury (skip confined: `crime/outcomes.py:246-256` "no income"), pay pensions to `age>=policy.pension_eligibility_age` at `policy.pension_*_pct` of last wage, honour a `reserve_floor_sim` policy field; `award_tender` escrows `budget_sim` to the awardee's business on award or on `build_structure` completion. Add tools `create_job`, `cut_job`, `post_tender`, `award_tender`, `bid_tender` (new: businesses bid; tenders have no bidding at all, `models.py:437-441` "bidding itself belongs to the not-yet-built business module"). Effort M. Acceptance: mock run with one police officer at 140/day shows `TransactionReason.WAGE` from treasury daily and `pensions_paid_sim>0` for a 70+ resident.

**GOV-09 (P1, world) Credit-store margin and pension policy are dead levers.**
`credit_store_margin_bps` is read nowhere outside charter and `governance_api` (grep); `buy_energy` uses the fixed `SIM_PER_USD_CENT` (`civic.py:284-330`). `pension_*` fields likewise. DESIGN §7 lists both as government powers. **Fix:** `Ledger.exchange_sim_for_usd` takes `margin_bps` and credits the margin to the treasury; `pay_pension` takes pct from policy. Effort S.

**GOV-10 (P1, world) Organisations cannot run: membership is founder-only forever, no treasury, no roles, no decisions, no rulesets.**
`Organization` (`models.py:598-606`) has `members` but `add_member/remove_member` (`organizations.py:91-116`) have no tool; ORGANIZATION-namespace visibility (`registry.py:328-331`) therefore admits only the founder; `define_organization_tool` is founder-only (`meta.py:482-495`). No ledger account is opened for an org (`register_org_handler` `civic.py:170-192` never touches the ledger; the 3000 sim is an action cost). No dues, no roles beyond `Literal["founder","member"]`, no org vote, no contracts, no `ruleset` anywhere (grep `ruleset` in `app/world`: nothing; ROADMAP T6 not started). **Fix:** see §5.2 work item. Effort M-L.

**GOV-11 (P1, world) Land expansion is operator-only and, on the production host, decision-only.**
`approve_petition` has no tool; only `admin_api.py:631-667` (operator token). `run_genesis.py:2178` passes `map_service=None`, so approval "records the decision, no physical map growth" (`admin_api.py:652-664`). Nothing subscribes to `politics.petition.approved` (grep: only the emitter), contrary to `land_petitions.py:22-27` and `models.py:548-556` which say a land module "is expected to consume" it. **Fix:** see §5.3. Effort M (charter side S; map side belongs to the growth reviewer).

**GOV-12 (P1, world) Checkpoint gaps: head-tax debt and crime store are not restored; `main.py` host does not checkpoint the charter.**
`HeadTaxDebtStore` (`run_genesis.py:3014`) has `snapshot/restore` (`head_tax.py:258-266`) but no `checkpoints.register` (grep). A resume keeps `world.arrears` (owned state) and empties `head_tax_debts`, so `repay_head_tax_debt` (`head_tax.py:458-520`) would raise `NoOutstandingDebtError` for a debt `check_debts` still shows. `CrimeStore` has no snapshot method. `wiring.py:1459-1461` registers only `world_state` and `map`; `WorldState` deliberately excludes `charter` (`state.py:327-361`) so a `main.py` resume re-seeds genesis governance, exactly STATUS risk #4 in the other host. **Fix:** register `HeadTaxDebtStore` in `run_genesis.py:3106-3126`; add `CrimeStore.snapshot/restore` + adapter; register `CharterCheckpointAdapter` in `build_world_server`. Effort S. Acceptance: existing `test_run_genesis` checkpoint round-trip extended to assert debt balances survive.

**GOV-13 (P2, registry) Suffrage age is hard-coded in the registry, so an amendment to `suffrage_min_age` cannot take effect.**
`civic.py:61` `MIN_CIVIC_AGE_YEARS=18.0` on `vote` (`ActorRequirements(min_age=...)`, `civic.py:~449`), `register_business`, `register_org`; `check_suffrage` (`elections.py:52-66`) reads the charter. Lowering suffrage to 16 by amendment changes nothing; raising it works. Also `declare_candidacy` has no min_age at the registry but goes through `check_suffrage`. **Fix:** drop the registry `min_age` on `vote` and let the charter decide; or make the registry read `charter.suffrage_min_age`. Effort S.

**GOV-14 (P2, world) Head-tax payer is whoever iterates first.**
`run_genesis.py:4719` `payer = heads[0]` where `heads` is insertion order of `world.agents` filtered by household; a minor can be the payer (and minors' sim balances may be 0, pushing the whole household into debt while an earner holds sim). `head_tax.py:29-38` explicitly leaves payer choice to the caller. **Fix:** pick the adult with the largest sim balance (or a `household.head_agent_id`). Effort S.

**GOV-15 (P2, world) Head-tax debt events are never emitted; `rule` fines are booked as tax.**
`run_genesis.py:4730` passes `event_sink=None`, so `economy.head_tax_debt.accrued` (`head_tax.py:428-441`) never reaches the bus/trace. `rule` uses `TransactionReason.TAX` for a fine (`institutions.py:373-381`), so `treasury_view.tax_collected_sim` (`governance_api.py:580-590`) and `family_api.money_flow` misreport fines as tax. **Fix:** pass the bus sink; add `TransactionReason.FINE`. Effort S.

**GOV-16 (P2, world) A jail sentence never ends on its own.**
`rule jail` sets `until_tick` (`institutions.py:386-389`); no tick sweep reads it (grep `until_tick`: only `registry.py:358`'s message). `crime/outcomes.py:270-297` has the sweep, unwired. Jailed residents stay `require_not_confined`-blocked from `move` until somebody calls `release`. **Fix:** in the tick loop, pop `world.confined[a]` when `until_tick <= tick` and emit `crime.release.recorded`. Effort S.

**GOV-17 (P2, world) Residents cannot discover an election or the government.**
No tool returns `election_id`, phase, candidates, or who sits (grep `election` in `places.py`, `people.py`, perception: nothing). `look_around` at the complex says "Register a business, pay tax, petition, vote" (run-23 lines 2390, 4245) but not that an election is open or who holds seats; `village_rules.survival.pressures_told_to_the_agent` promises "any petition you signed and whether it was answered", which is browser-only. **Fix:** add an election/government block to `describe_place`/`look_around` at the complex/town hall and a `civic_notice` line in the perception (open election + phase + your ballot status + amendments awaiting your vote if seated + tax rate + arrears). Effort S. Acceptance: L1 scenario "it is day 2, an election is open, you are at town hall" reaches `vote` >= 60%.

**GOV-18 (P2, frontend/world) Two rulebooks.**
`village_rules.json` (`economy.tax_rate_bps 300`, `government.salaries_per_day_sim`, `term_days 90`, `treasury.reserve_floor_sim 2000`, `documents.*`, `unrest`) is validated by `rules/loader.py` and served to the browser, which computes its own economy/government (HANDOVER §7.3 item 8). The world reads `Charter`/`GovernmentPolicy` (`term` = 3 days, tax 0, no salaries). The viewer's Council/treasury numbers can disagree with the ledger's. **Fix:** either make the world read `village_rules.json` as the genesis source for `GovernmentPolicy`/salary schedule (and delete the JS duplicates), or mark the JSON viewer-only in its `_README`. Effort S.

**GOV-19 (P2, world) `social_api` is not mounted on the production host.**
`run_genesis.py:2186-2226` mounts agents, location, governance, family, skills, workshop; `build_social_router` only at `main.py:199`. The scenario/special-event feed DESIGN §8 asks for is unreachable in a real run. Effort S.

**GOV-20 (P2, world) `declare_candidacy` wakes the whole town.**
`institutions.py:167-170` `involved_agent_ids = sorted(world.agents)`; `perception.py:52` treats that key as an interrupt list. One candidacy = N extra model calls at ~$0.006 each (run 23 per-turn cost). With 20 candidates at 250 residents that is 5000 wakes. **Fix:** publish a notice line instead (GOV-17) and keep `involved_agent_ids` to the candidate. Effort S.

**GOV-21 (P2, world) `arrest` moves the accused to a location the map does not draw.**
`LOCATION_JAIL = LOCATION_TOWN_HALL = "town_hall"` (`institutions.py:54`); map sectors are "Kasukabe Government Complex" (`map/expansion.py:56`). `LocationIndex` will hold `"town_hall"`, gates fold it, but `look_around` will report `tile: null` and the movement system has no tile for it (run-23 shows the same null-tile pattern for "temple"). Effort S: use the sector name.

**GOV-22 (P2, world) Constitutional complaint ledger has no writer.**
DESIGN §7's "immutable complaint ledger" is one of the two genesis checks; `ComplaintLedger.file_complaint` (`complaints.py:33-63`) is uncalled. **Fix:** either a `complain_about_government(against_id, kind, text)` public tool, or route `file_complaint` with `against_kind="government"|"official"` to it. Effort S.

**GOV-23 (P3, world) Patent fee is checked twice and licence fees never move.**
`define_tool` debits the fee (`meta.py:211-225`) and `file_application` re-validates the amount (`patent_office.py:60-65`) - fine. `issue_license` records `fee_paid_sim` but never debits (`government.py:242-271`), so a future tool must remember to. Effort S, note only.

**GOV-24 (P3, tests) Tests that cannot fail on the production path.**
`charter/tests` and `crime/tests` (198) test engines with no runtime caller; `test_full_election_then_government_action_end_to_end` (`test_charter_engine.py:34-70`) passes `now_tick=300/500` by hand, which is exactly what production never does. There is no test asserting that `GenesisRunner` advances or tallies an election, no test asserting a CrimeEngine exists on the production host, and the S1 test (if any: grep `assert_the_day_was_charged` in `world/tests` found nothing) cannot distinguish a zero bill. `test_track_wiring.py` is the right home for "engine X has a production caller" assertions.

**GOV-25 (P3, world) `EligibilityFacts.is_resident` = "has a household"** (`civic.py:66-75`). A newborn or a resident whose household dissolved becomes a non-resident and loses the vote silently. Note only.

---

## 4. Gaps vs DESIGN.md

| DESIGN clause | State |
|---|---|
| §7 Elections every 3 game-days, 1 PM + 4 ministers, in person at town hall | Models and windows exist; **never tallied, never re-scheduled** (GOV-01). Town hall == government complex on the map (`place_aliases.py:83-91`). |
| §7 Government powers: taxes | Tool exists, uncallable (GOV-01); rate 0 (GOV-06). |
| credit-store margin | Engine only; not applied by `buy_energy` (GOV-09). |
| jobs | Engine only, no tool, no payroll (GOV-08). |
| permits | Engine only, no tool; blocks all building (GOV-07). |
| pensions | `pay_pension` uncalled; policy ignored (GOV-08/09). |
| police | `appoint_police` tool; no salary; three definitions (GOV-05/08). |
| tenders | Engine only; no bidding; no money (GOV-08). |
| buildings | `build_structure` exists, unreachable (GOV-07). |
| land petitions to the President | Tool exists (seat-gated, uncallable); operator approval works; map growth only on unused host (GOV-11). |
| amendment by 3/5, election rules, collapse to one-person rule | Engine complete; **no vote tool** (GOV-03). |
| §7 Genesis checks: immutable complaint ledger + Presidential veto | Veto works (`admin_api.py:699-710`); ledger has no writer (GOV-22). |
| §7 Organisations first-class: parties, cults, societies, religions, registered or underground | Row-only (GOV-10). Underground coordination "detectable in the graph": no graph analysis exists (research/ has only `__init__.py`, `demographics.py`). |
| §7 Temple starts keeperless; community chooses keeper; weddings, prayers, donations | Weddings and prayers work; donations go to the **treasury**, not a temple (`social.py:420-431`); no keeper concept anywhere (grep `keeper`: only persona opinion roles in `genesis_20.json:761`); no rite tool; `religion.doctrine.emerged` is mapped in `social_api.py:130` but nothing emits it. |
| §6 Theft ~70/30 | Both implementations exist; live one records against the victim (GOV-02). "Inventory" theft only; wallet theft unmodelled (`primitives/crime.py:3-11`). |
| §6 Complaints desk -> case file -> police investigate (interviews, public records, NOT omniscient) -> minister-signed warrant -> fine or jail | Built in `world/crime/`, unwired (GOV-05). Live path: complaint -> arrest -> seat rules; no investigation, no warrant, no minister signature, and the `crime.theft.attempted` payload carries `thief_agent_id` to whoever perceives it (`primitives/crime.py:88-97`), so police knowledge == ground truth. |
| §6 Fights injure, killing excluded | `fights.py` built, no tool. |
| §6 False-conviction rate as flagship metric | `crime/metrics.py` over an empty store; live path has no ground-truth link (GOV-02). |
| §8 Governance viewer: charter + amendments, active policies, change log with tool call, proposer, votes, diff | `governance_api.py` serves all of it (diff at `:305-330`, votes at `:337-420`); `enacting_action` is honest that no tool exists (`:380-398`). Empty in practice. |
| §8 Voting viewer: live election, running tally, transfer of power, history | `governance_api.py:794-870`; shows one election stuck in `voting_open`. |
| §8 President console: petitions, veto, address, honors | Present (`admin_api.py:624-790`). |
| §8 God console: prayers grant/silent-deny, blessings, disasters, miracles, dials | Present (`admin_api.py:792-1108`). |
| §9 trace tables `organizations, petitions, cases` | Schema has them (`sqlite_schema.sql`); `TraceStore` writes agents/businesses/events/transactions/utterances/memories/skills/tools_registry/world_snapshots only (`wiring.py:679-1082`); governance lives only in `CharterStore` memory (`governance_api.py:1-22` says so). |
| §3 "Agents must be able to see ... today's upkeep and tax ... queryable before acting" | No perception line for tax rate or arrears on the runner path (grep `arrears`/`head_tax` in perception rendering: none); `check_debts` reads arrears (`labour.py:295`). |
| ROADMAP T3 "every institutional action callable and gate-checked" | Missing tools: table/vote amendment, create/cut job, issue/revoke permit and licence, post/award/bid tender, interview, warrant request/sign, join/leave/expel org, org dues/vote, ordain keeper, hold rite, fight. |
| ROADMAP T6 rulesets; Charter as the government's ruleset | Not started (grep `ruleset`: nothing). |

---

## 5. Gaps vs the target, and the designs asked for

### 5.1 Q1 The election cycle
**Answer:** manual, and not even manually completable from inside the town. Scheduled once at boot; phases advance lazily inside `declare_candidacy`/`cast_vote`; `tally` uncallable; no re-schedule; no resident is told. Nothing has ever run live: zero candidacies, zero votes, in 23 runs (§1.4). All runs ended by tick 83, before voting could open at tick 288, so `vote` has never been callable either. Build on: `elections.py` is complete and idempotent by design (`elections.py:13-22`). Add: `GenesisRunner.charter_tick` (GOV-01), civic notice line (GOV-17), rewired sink (GOV-04). Consider shortening genesis windows (0.5/0.5/0.5 day) so a 2-game-day rung-1 run sees a full cycle.

### 5.2 Q4 Organisations that RUN
Exists: `Organization` row, ORGANIZATION namespace ACL (`registry.py:328-331`), `define_organization_tool` (`meta.py:471-530`), org-scoped tool visibility. Missing, in order:
1. **Membership tools** (`join_org`, `leave_org`, `admit_member`/`expel_member` by a role-holder) wrapping `organizations.add_member/remove_member` (S).
2. **Org ledger account**: open `AccountKind.ORGANIZATION` in `register_org` (mirror `Ledger.register_business`, `ledger/core.py:670-687`); `pay_dues(org_id, amount)`, `org_disburse` by treasurer role (S-M).
3. **Roles**: replace `Literal["founder","member"]` with `dict[agent_id, role]` where roles are org-defined strings plus a `powers` map (who may admit, disburse, amend) (S).
4. **Decisions**: `org_propose(org_id, field|text)` / `org_vote` with a threshold in the org's ruleset; enacted changes emit `politics.organization.amended` (M).
5. **Rulesets (T6)**: one `Ruleset` model used by orgs, businesses and the charter (`standing_orders` already is a proto-ruleset field): `{who_may_call: {tool: predicate}, obligations: [dues, revenue_share, wage_floor, price_floor], on_breach: event}`; enforce in `ToolRegistry.execute` after actor checks (`registry.py:335-380`) and emit `ruleset.breach` to the complaints desk (L). Contracts: a `contract(parties, terms, settlement_tool, deadline)` object with `reconcile_wages`-style day check (`run_genesis.py:4752-4796` is the pattern) (M).
6. Temple as an organisation of kind RELIGION whose ruleset names the keeper role; `ordain_keeper` = org decision.
Acceptance (T6 gate): org registers ruleset; member call permitted, non-member refused; breach reaches the desk; ruleset amended by the org's own vote and takes effect next call.

### 5.3 Q5 Land petitions and expansion approval
**Path today:** seat-holder standing in the government complex -> `petition(title, description, tile_count)` (`civic.py:102-125`, 100 sim; GOVERNMENT namespace so seat-gated twice: `civic.py:459-461` and `land_petitions.py:60-62`) -> `LandPetition PENDING` -> handler emits `politics.petition.filed` (`civic.py:111-121`) -> operator console `GET /admin/president/petitions` (`admin_api.py:624-629`; console `world_client.py:157-171`) -> `POST .../approve {decision_note, edge, depth, new_sector_name, arena_names}` (`admin_api.py:631-667`) -> `approve_petition` flips status and emits `politics.petition.approved` (lost on run_genesis, GOV-04) -> **inline** `MapService.expand_land(...)` only when a map service is wired (`main.py` host); on run_genesis `map_service=None` -> warning, no growth. Nothing subscribes to the approval event. Who files = only sitting seats (never anyone so far); who approves = the operator only; the President is the operator by DESIGN §1/§8.
**Can the town approve its own expansion?** Not today: `approve_petition` has no tool and no seat check (it is an operator method). **Should it?** Recommend a two-tier rule, amendable: (1) the government passes an `expansion_resolution` by its own vote (reuse the amendment machinery: a proposal of `field_name="expansion"` or a dedicated `LandResolution` with the 3/5 threshold); (2) the President keeps a veto window of N ticks (like amendments' sign/veto), after which the resolution auto-approves; (3) approval does not stamp tiles - it opens a **tender** (`post_tender`) whose award and `build_structure` completion by the town's builders, from materials in a storage, is what calls `expand_land`. That makes "the town's own builders construct" literal and keeps the operator as a brake, not a bottleneck. Charter side: S; tender/construction linkage: M; map: growth reviewer.

### 5.4 Q3 IDEOLOGY -> MECHANICS: the policy engine

**What can change a mechanic at dispatch time today.** Exactly four reads: (1) `GovernmentPolicy.tax_rate_bps` at the day boundary (`run_genesis.py:4704`); (2) `Charter.standing_orders` in `WorldState.is_followable` (`state.py:1195-1212`), which gates walking a governmental workflow; (3) `Charter.patent_filing_fee_sim` in `define_tool` (`meta.py:211`); (4) suffrage/term fields inside the election engine. `credit_store_margin_bps`, `pension_*`, `permit_fee_sim`, `builder_license_fee_sim` are read by nothing that moves money. `village_rules.json` has `price_cap_multiplier`, `wage`, `treasury.reserve_floor` concepts but only the browser reads them.

**Design: `PolicyBook`** (new `charter/policy.py`; extends, does not replace, `GovernmentPolicy` so `policy_history` and the amendment path carry it). Frozen, versioned, one snapshot per change, exactly like `GovernmentPolicy` (`models.py:199-236`). Fields, each with a default that reproduces today's free market:

| Field | Type | Read by (file:function) | Effect |
|---|---|---|---|
| `tax_rate_bps` | int | `run_genesis.charge_the_day_tax` (exists) | head tax |
| `income_tax_bps` | int | `labour.pay_wage_handler`, `commerce.buy/sell/trade` settlement | withheld to treasury on wage/sale |
| `credit_store_margin_bps` | int | `civic.buy_energy_handler` -> `Ledger.exchange_sim_for_usd` | margin to treasury |
| `price_caps: dict[item, int]` | sim ceiling per unit | `commerce.post_price_handler` (refuse above cap), `commerce.buy/sell/trade` (refuse settlement above cap) | price controls |
| `price_floors: dict[item, int]` | | same | supports |
| `wage_floor_sim` | int/day | `labour.set_wage_handler`, `reconcile_wages` | minimum wage |
| `transfer_ceiling_sim`, `transfer_floor_sim` | int | `commerce.transfer_handler`, `living.give_handler` | capital controls / forced charity floor |
| `rationing: dict[item, {per_head_per_day: int}]` | | `commerce.buy_handler` (quota counter in `WorldState.ration_ledger`, reset at day boundary) | rationing |
| `property_transfer_rule` | enum `free|licensed|forbidden|collective` | `property.transfer_tile/auction_land`, `civic.transfer_business` | property rules |
| `collective_ownership: list[kind]` | e.g. `["mill","farmland"]` | `commerce.buy/sell` (proceeds to treasury or org account), `resources.harvest` (yield to a common storage) | collective ownership |
| `business_licensing` | enum `open|permit_required|state_only` | `civic.register_business_handler` | licensing |
| `relief_payment_sim`, `relief_threshold_sim` | int | new `run_treasury_day` | welfare to residents below threshold |
| `closed_borders` | bool | `wiring.god_grant_birth_request` / any future immigration; `movement.move` to expansion sectors | borders |
| `pension_*`, `salary_schedule: dict[job_type,int]`, `reserve_floor_sim` | | `run_treasury_day` | spending |
| `emergency_powers: bool` | | bypasses `reserve_floor_sim` and tender wait | crisis |

**Reader plumbing.** One helper `world.policy()` returning the current `PolicyBook` (cached per tick), and one registry hook `apply_policy_gates(spec, world, actor, params)` called in `ToolRegistry.execute` between location gate and handler (`registry.py:584+`) so that caps/floors/licensing/rationing refusals are uniform `PolicyRefusal(ToolError)` with the rule quoted (a `correction` signal per T4). Money effects (income tax, margin, collective proceeds) live in the handlers named above because they need the ledger. Day effects live in `run_treasury_day` (GOV-08).

**Amendable by vote.** Two paths: routine levers (tax, margin, price caps, wage floor, relief) via a seat tool `set_policy(field, value)` (generalising `set_tax_rate`, `government.py:83-100` pattern); structural ones (property rule, collective ownership, licensing, borders, election rules) via the 3/5 amendment path (GOV-03), which means moving those fields onto `Charter` or letting `AmendmentProposal.field_name` address `policy.<field>`. Every change: `politics.policy.set` event with `{field, old, new, by, version, proposal_id?}`, appended to `policy_history`; `governance_api` already diffs charters (`:305-330`), so extend `diff_charters` to policies for the change log.

**Ideology of citizens: representation and measurement.** Today there is no belief field anywhere: personas carry prose attitudes (`attitude_authority`, `religion` lines in `genesis_20.json`), the engine keeps an `evolved_soul` table of `(trait, value, status, confidence, evidence_count)` (`engine/src/memory/soul.py:64-130`) written by the learning centre (`pipeline.py:777,838,851`), and the sim seam registers only the static persona section (`pinned_boot.py:468-486`), so the evolved soul is never even shown to the resident (T1 DYNAMIC layer unbuilt). Proposal, all in the seam/world (no engine change):
1. **Axes** (research-facing, never shown to residents as labels, to keep Earth-leakage measurable): `redistribution` (-1 keep / +1 share), `state_control` (market / command), `property` (private / collective), `openness` (closed / open), `authority` (pluralist / strongman), `piety`.
2. **Three measurements per resident per game-day**, stored as `beliefs` rows in the trace: (a) **behavioural**, computed from `transactions`/`tool_invocations` (share of income given via `give`/`transfer` to non-kin; compliance with caps; votes on amendments by field; prayers/donations); (b) **stated**, from the engine's `evolved_soul` traits projected onto the axes by a fixed keyword/embedding map, read via the seam (`Soul.get_evolved_soul`) at sleep; (c) **probed**, DESIGN §10's dream probes: a fixed 6-question battery on the background lane during sleep ticks, never written to memory - `app/research/` has no such module yet.
3. **Elections reflect it** by giving candidacy a platform: `declare_candidacy(election_id, platform: dict[policy_field, value])` stored on `Candidacy`; `vote` records the ballot; the research join is `ballot x platform x voter beliefs`. After tally, the winning platform is auto-tabled as amendments/policy changes (still voted by the seats) so a mandate has a mechanical consequence.
4. **Drift** = axis distance between genesis prose (scored once by the same map) and (a)+(b) over days; that is ROADMAP compounding quantity 4 made measurable.

### 5.5 Q6 Crime and justice
Answered in GOV-02/05/16 and §4. What has ever run: nothing (zero `steal`, `file_complaint`, `arrest`, `rule`, `release` in 23 runs). Ground truth vs police knowledge on the live path: identical (payload leaks thief). False-conviction metric: only in the orphaned package.

### 5.6 Q7 Temple/religion
Keeper selection: nothing. Rites: nothing beyond `marry`. Prayers: `pray` -> `PrayerRequest` -> operator grant (mint) / silent deny; 10 prayers in run 23, none answered (no god-console action recorded). Donations: to treasury. Recommend: temple = organisation of kind RELIGION seeded at genesis with empty membership and a `keeper` role; `ordain_keeper` by org vote or by charter amendment (the "method is theirs" clause); temple ledger account receiving donations and wedding fees; `hold_rite(kind, text)` emitting `religion.rite.held` with attendees = co-located; `religion.doctrine.emerged` emitted when `skillify`/`toolify` is filed in the ORGANIZATION namespace of a RELIGION org.

### 5.7 Q8 Checkpoint
GOV-12.

### 5.8 Other target dimensions
- **Thriving, not surviving:** with tax 0, no salaries, no pensions, no relief, the treasury is a sink for fees and a source of USD for `buy_energy` only; nothing the government does changes anyone's life. GOV-06/08/09 and the PolicyBook are the minimum.
- **Skills/tools cutting model calls:** institutions contribute `standing_orders` (governmental workflows) which are the only charter field with a runtime effect; unblocking GOV-03 makes them enactable.
- **Instrumentation:** governance lives only in memory (`CharterStore`) and the trace has no `petitions/organizations/cases` rows; the false-conviction metric has no data; politics events are dropped on the production host. GOV-04 + a `TraceStore.record_charter_*` writer for the schema tables that already exist are the fix.

---

## 6. Recommended work items (priority order)

1. **Election tick job** (GOV-01, GOV-04, GOV-17). Spec: `GenesisRunner.charter_tick` runs every tick: `advance_phase` on the open election; at `voting_closes_tick` call `tally`; schedule the next election at `tallied_tick + interval*144 - (windows)`; rewire the charter sink to the bus in `boot`; render a civic notice line (election phase, candidates, your ballot, tax rate, arrears, seats) in the perception; expose `list_elections` in `look_around` at the complex. Acceptance: mock 3-day run -> PM seated, second election scheduled, `politics.election.tallied` in trace; L1 "open election, at town hall" reaches `vote` >= 60%.
2. **Fix case polarity and ground truth on the live justice path** (GOV-02, GOV-16). Spec above. Acceptance above plus `until_tick` auto-release test.
3. **Amendment tools** (GOV-03, GOV-13). `table_amendment`, `vote_amendment`; drop registry-side suffrage hard-code; operator veto window with auto-sign after N ticks configurable. Acceptance: charter v1 reached without operator action when `KASUKABE_AUTO_SIGN_TICKS` set; refused by veto otherwise.
4. **Permits, licences, jobs, tenders as tools + treasury day job** (GOV-07, GOV-08, GOV-09, GOV-15). Acceptance: police wage paid daily; builder licensed and permit issued via tools; `build_structure` succeeds; fines booked as FINE; tender awarded escrows budget.
5. **PolicyBook** (§5.4). Ship the four cheapest levers first: `price_caps`, `wage_floor_sim`, `income_tax_bps`, `relief_payment_sim`; then property/collective/licensing/borders. Acceptance: a `post_price` above cap is refused with the rule quoted and a `correction` signal written; relief paid to under-threshold residents at day boundary; change log shows old/new/by/version.
6. **Organisations that run** (GOV-10, §5.2 items 1-4). Acceptance: two residents join an org, pay dues into its account, a role-holder disburses, a non-member is refused an org tool.
7. **Wire or delete `world/crime/`** (GOV-05). If wired: construct in `run_genesis`, add interview/warrant tools, checkpoint the store. Acceptance: R3 gate (complaint -> investigation -> warrant -> verdict with ground truth).
8. **Checkpoint completeness** (GOV-12). Register `HeadTaxDebtStore`; `CrimeStore.snapshot`; charter adapter in `build_world_server`. Acceptance: round-trip test.
9. **Rulesets (T6)** with the Charter as the government's ruleset; temple as a RELIGION org with keeper role (§5.2 item 5, §5.6).
10. **Land expansion resolution + veto window + tender** (§5.3) with the growth reviewer.
11. **Hygiene**: single rulebook (GOV-18), mount `social_api` (GOV-19), candidacy wake fan-out (GOV-20), jail location name (GOV-21), constitutional complaint writer (GOV-22), payer selection (GOV-14), production-path wiring tests (GOV-24).

---

## 7. Open questions for the operator

1. Should the operator's role as President remain a hard gate on amendments and land, or become a veto window with auto-approval? (Affects whether a run can ever be "entirely on its own".)
2. Genesis tax rate and salary schedule: use `village_rules.json`'s 300 bps / 140-260 sim per day as the world's genesis policy, or keep DESIGN §3's "no tax at start" and let the first government set it?
3. Election windows: keep 1/1/1 days (voting opens day 2) or shorten so rung-1 (2 game-days) sees a complete cycle?
4. Justice: wire the built `world/crime/` epistemic pipeline (interviews, warrants, minister signature) or accept the simpler seat-rules model and grow it? The false-conviction metric needs suspect != ground truth either way.
5. Should the government namespace hide `petition` from non-seats (current), given `village_rules.documents.petition.filed_by: any_resident`? A citizen petition tool is a different object from a land petition to the President.
6. Ideology axes and the dream-probe battery: are the six axes in §5.4 acceptable as research variables, and is a background-lane probe per resident per sleep affordable at rung 2 (50 x 1 call/day on DeepSeek)?
7. Temple: seed it as a registered RELIGION organisation with a `keeper` role and an account, or keep it as a place and let residents `register_org` a religion themselves?
8. Is the browser sim's government (salaries, unrest, documents, build queue) meant to converge with the world server, or to be retired once the world's institutions run?
