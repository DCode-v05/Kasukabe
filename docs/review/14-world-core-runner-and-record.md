*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`CORE-nn`) are referenced from the cross-cutting documents.*

# CORE — the run itself (runner, world composition, tick loop, perception, trace, checkpoint, supervision, observability)

Read-only review of `Real` @ 5400e94 (2026-09-03). Every claim cites `path:line`;
where I infer rather than observe I say so. Scope: `app/world/run_genesis.py`
(all 5,492 lines read), `wiring.py`, `main.py`, `app.py`, `core/*`,
`preflight.py`, `namespace_supervisor.py`, `memory_budget.py`, `resource_log.py`,
`signal_groundskeeper.py`, `retrieval_depth.py`, `run_report.py`, `admin_api.py`,
`agents_api.py`, `location_api.py`, `agent_viewer_api.py`, `app/tests/*`, the
four genesis generators, `genesis_20.json`, `docs/TEST_RUN.md`,
`docs/RUN_REPORT.md` (first 300 lines), plus the engine seam files the run path
crosses (`engine/src/sim/{sim_main,agent_adapter,pinned_boot,world_tools,
learning_gate,world_corrections}.py`) and the run-23 report.

**Test run.** `cd app && uv run pytest world/core world/tests -q` finishes in
~60 s: **1035 passed, 69 failed, 6 skipped**. All 69 failures are
environmental: 66 raise `RuntimeError: Ville asset directory not found`
(`wiring.py:1426`) because `smallville/` is not present in this checkout
(`ls /home/user/kasukabe` has no `smallville`), 2 more depend on the same map
(`test_the_fifty_are_a_town.py:217` needs `known_places`,
`test_group_chat_reaches_the_screen.py:91` needs the market square as a
place), and `test_preflight.py:587` fails because the sandbox runs as root, so
an "unwritable" directory is writable. No failure in the runnable subset points
at a code defect; note, though, that the suite cannot prove the map path on
this machine at all.

---

## 1. Inventory

Status legend: LIVE = on the production path from `python -m world.run_genesis`
(the only launcher used by `runseries.sh:87-94` and `start-genesis.sh:41-49`);
TEST-ONLY; ORPHANED = no production caller.

| Path | Lines | Purpose | Status (proof) |
|---|--:|---|---|
| `app/world/run_genesis.py` | 5,492 | Config, supervisor, gateway, checkpoint adapters, embedded FastAPI world app, `GenesisRunner` (boot/tick/life/day jobs/drain/shutdown), CLI | LIVE (`main_async` 5449-5484) |
| `app/world/wiring.py` | 1,498 | `TraceStore` (LIVE via `run_genesis.attach_persistence` 3231-3249) and `WorldServer`/`build_world_server` (the "real" server) | `TraceStore` LIVE; `WorldServer`, `build_world_server`, `apply_genesis_population`, crime adapters, `rewire_charter_event_sink` are **only reached from `world/main.py`**, which no run launches → ORPHANED on the run path (used by tests `test_world_wiring.py`, `app/tests/test_console_world_integration.py:324`) |
| `app/world/main.py` | 653 | Standalone world server (`uvicorn world.main:app`); mounts admin/agents/location/governance/social/agent-viewer/family/skills/workshop routers, `/ws/events`, `POST /tools/{tool_name}` | ORPHANED on the run path: no launcher, and it does not serve the two routes engines need (`POST /agents/{id}/tool_calls`, `/agents/{id}/memories`) — see CORE-01 |
| `app/world/app.py` | 31 | Health-only scaffold | ORPHANED (own docstring 654-663 in the read; kept for `test_world_app.py`) |
| `app/world/core/engine.py` | 192 | `TickEngine.tick()`: clock → movement → due → perception → day checkpoint | LIVE (`run_genesis.py:3087-3105`, `4280`) |
| `app/world/core/clock.py` | 118 | 10-min ticks, 144/day, day 06:00-22:00 | LIVE |
| `app/world/core/scheduler.py` | 196 | Round-robin cadence + interrupts | LIVE (`engine.py:75,146`) |
| `app/world/core/perception.py` | 412 | `LocationIndex`, `PerceptionSystem.build_perception` | LIVE (`engine.py:77,147`) |
| `app/world/core/events.py` | 205 | `EventBus` (2000-entry ring), `WorldEvent` validation | LIVE (`bootstrap.py:293`) |
| `app/world/core/movement.py` | 449 | `MovementSystem` walked routes, checkpointable | LIVE only when Ville assets exist (`bootstrap.py:152,312`; `run_genesis.py:3104`) |
| `app/world/core/pacing.py` | 103 | `PacingController` continuous/burst | Constructed (`engine.py:110`) but its `run_continuous/run_burst` are **never called by the runner** — the runner has its own loop (`run_genesis.py:5171-5231`). Only `config.continuous_interval_s` is written (4038) and read back by the console (`admin_api.py:580`). ORPHANED behaviour → CORE-05 |
| `app/world/core/checkpoint.py` | 150 | `CheckpointManager` | LIVE (`engine.py:82,164`) |
| `app/world/preflight.py` | 681 | 15-20 pre-boot / live checks | LIVE (`run_genesis.py:3890-3909`, `2305-2364`) |
| `app/world/namespace_supervisor.py` | 264 | In-process engines, one `SEPTEMBER_HOME` each | Selectable (`--supervisor-backend namespace`) but cannot host a real engine (config docstring `run_genesis.py:518-535`, `docs/TEST_RUN.md` "Run 2 §2"); its gateway would crash on first dispatch (CORE-24). Effectively ORPHANED |
| `app/world/memory_budget.py` | 380 | RAM verdict before spawn, measured MB/engine | LIVE (`run_genesis.py:3932-3957`, `preflight.py:409-450`) |
| `app/world/resource_log.py` | 434 | Per-agent RSS/CPU sampler (JSONL) | LIVE only with `--resource-log` (`run_genesis.py:3826-3833`, `4294-4320`) |
| `app/world/signal_groundskeeper.py` | 191 | Deletes `system_event` learning signals every 300 s | LIVE (`run_genesis.py:3773-3776`) |
| `app/world/retrieval_depth.py` | 1,155 | Depth knob + `render_perception_text` | LIVE (`run_genesis.py:4350-4442`) |
| `app/world/run_report.py` | 1,465 | Markdown run report from live world + trace db | LIVE as a side process (`runseries.sh` / operator) |
| `app/world/admin_api.py` | 1,240 | `/admin/*` President/God console contract | LIVE (`run_genesis.py:2182-2183`) |
| `app/world/agents_api.py` | 558 | `/agents`, `/businesses/pnl`, `/places`, `/map/tiled`, `/world/state` | LIVE (`run_genesis.py:2196-2200`) |
| `app/world/location_api.py` | 741 | `/places*`, `/world/state` (second definition!) | LIVE (`run_genesis.py:2201-2204`); route collision with `agents_api` — CORE-30 |
| `app/world/agent_viewer_api.py` | 648 | `/agent-viewer/*` finder/activity/reasoning | **NOT mounted by the embedded app** (only `main.py:200`) → ORPHANED on the run path; `run_report.py:783-786` already works around it |
| `app/world/make_genesis_20.py` | 769 | Ten authored residents added to `genesis_10.json` → `genesis_20.json` | Generator (offline) |
| `app/world/make_genesis_50.py` | 341 | `genesis_20` + 30 carved households → `genesis_50_town.json` | Generator |
| `app/world/make_genesis_neighbours.py` | 679 | The authored ten → `genesis_10.json` | Generator |
| `app/world/make_genesis_subset.py` | 324 | Carve N whole households from `genesis.json` | Generator |
| `app/world/genesis_20.json` | — | 20 agents, 4 households, construction company + 3 small businesses, `usd_ledger` 600¢ × 20 + treasury, `ablation_summary` | LIVE cast for runs 19-23 |
| `app/tests/test_console_world_integration.py` | 370 | Console client vs embedded app (real uvicorn) and vs `main.py` | TEST-ONLY (the `main.py` half needs the map) |
| `app/tests/test_module_boundaries.py` | 74 | boto3 boundary | TEST-ONLY |
| `app/tests/test_ledger_dollar_invariant.py` | 62 | $1000 invariant | TEST-ONLY |
| `app/observatory/` (`:8500`) | — | Refusal-gap observatory | Separate process, not started by any script I found; run 23's instrument table says "Observatory ✗ not reachable on :8500" |

---

## 2. How it works today (the live path, file:line)

### 2.1 Boot → genesis → persona files → engine processes

1. `python -m world.run_genesis --genesis-path world/genesis_20.json --world-mode embedded --supervisor-backend process --engine-repo ../engine --no-resume --start-at-dawn --tick-interval-s 15 --budget-cap-cents N` (`runseries.sh:87-94`). `config_from_args` (5399-5446) overlays CLI on `GenesisRunnerConfig.from_env` (629-705); `SPEND_CAP_USD` becomes the runner's own cap (651-666).
2. `GenesisRunner.boot()` (3070-3200): `build_world(genesis_tick=0, seed)` (3074) gives `WorldState`+`ToolRegistry` (bootstrap builds the bus at `bootstrap.py:293`, attaches `world.locations` at 295, ledger sink at 297, charter with **no sink** at 298, `MovementSystem` only if the Ville dir exists at 152/312). `TickEngine` is built at 3087-3105 with `tick_index=DAWN_TICK(36)` when `--start-at-dawn`, `hearing_window_ticks` from the depth knob, and `movement_system=world_state.movement`. Checkpoint components are registered: `ledger`, `world_state`, `world_locations`, `runner_counters`, `charter`, `records` (3106-3126) on top of the engine's own `clock`, `scheduler`, `locations`, `movement` (`engine.py:97-108`).
3. The genesis file is parsed as `RichGenesis` (3141-3145); names/bios/occupations/workplaces are pushed into `WorldState` (3159-3182, `WORKPLACE_BY_OCCUPATION` 213-248).
4. `attach_persistence()` (3202-3300) opens the trace SQLite at `TRACE_SQLITE_PATH` and subscribes one `persist` handler to `"*"` that records transactions, utterances, skills, invented tools and memories — **not `events`** (3216-3223, deliberate).
5. Resume or seed (3188-3198). `seed_genesis` (3335-3502) funds the ledger from the genesis file (3366-3373), builds `AgentLifeRecord`s, seeds trace `agents` rows (3395-3407, failures swallowed), places every agent at `home:<household>` or the market square (3421-3426) in **both** `world_state.locations` and the engine's `LocationIndex`, seeds businesses + opening stock (3438-3479), schedules the first election (3485), then `apply_tick_pacing()` (3487).
6. `start_embedded_world_server()` (4206-4251) builds `build_embedded_world_app` (2102-2921) and serves it with uvicorn on :8100.
7. `start_agents()` (3795-4026): `ProcessSupervisor` (3824-3825), ports 9100+i (3834-3835), `HttpAgentGateway` with `timeout_s=tick_timeout_s` (3853-3855), **persona.txt written per agent** from `build_persona_prompt`+`build_soul_seed` into `<home_root>/<agent_id>/persona.txt` (3869-3878); preflight (3890-3909); memory verdict (3932-3957); one `python -m src.sim.sim_main` per agent with `AGENT_ID`, `WORLD_BASE_URL`, `KASUKABE_HOME_ROOT`, `SIM_AGENT_PORT`, `BROKER_BASE_URL`, signed `BROKER_API_KEY`, `KASUKABE_ABLATION_ARM` (3959-4001); `wait_for_engines_ready` polls `/health` (4143-4204).
8. Engine side: `sim_main.run` (`engine/src/sim/sim_main.py:66-80`) → `boot_sim_engine` (`pinned_boot.py:339-608`): SQLite, compaction threshold 0.12 (297), `WorldClient` fetches `GET /tools/registry?agent_id=` (`world_tools.py:137-163`), persona section registered as STABLE (476-506), `Coordinator` with pinned thread, `SimLearningGate(awake_interval_ticks=…, learns=…)` (561-571), corrections store (583-585). `build_sim_agent_app` serves `/tick`, `/learn`, `/health` (`agent_adapter.py:101-182`).
9. `start_watchdog()` (3749-3793): `SupervisorWatchdog` (846-960), `SignalGroundskeeper`, and a one-shot memory measurement after 120 s.

### 2.2 The tick loop → due agents → perception text → POST /tick → tools → events → recorder

10. `run_continuous()` (5171-5209) loops `run_tick()` with **no wall-clock sleep** (`asyncio.sleep(0)` at 5209) until `stop_requested`, governor trip, a broker terminal state (only probed when `barren_ticks>0`, 5186-5190), 20 barren ticks (5191-5202) or `max_game_days`.
11. `run_tick()` (4255-4292): pause gate (4274) → live depth (4278) → `TickEngine.tick()` (4280): clock+1 (`engine.py:140`), movement advance publishing per-tile `agent.moved` (142-144), `due = scheduler.due_agents()` (146; cadence 3 + interrupted), perceptions for due agents (147-150), `world.tick.advanced` (152-161), day-boundary checkpoint at `tick % 144 == 0` (163-173). Then `dispatch_due_agents` (4283), `run_life_ticks` (4284), burn sample, resource sample, `governor.check()` (4289-4291).
12. `dispatch_due_agents` (4322-4495): for each due agent `one()`; a resident under `keep_doing` gets `advance_continuing` instead of a model call and returns **without** `mark_agent_decided` (4335-4338, so it stays due every tick — one repetition per tick); otherwise `is_sleeping = not timestamp.is_daytime` (4342), `note_turn` (4348), `render_perception_text(...)` with brief, playbook, hints, open questions, awaiting replies, wallet line, wake reason, conversation, silent-turn report, price board, offers, intention, convening hint, gap notice (4349-4445), then `asyncio.wait_for(gateway.send_tick(..., call_budget), tick_timeout_s)` (4447-4457); `mark_agent_decided` in `finally` (4474). All due agents run in one `asyncio.gather` (4477). Barren accounting 4490-4495.
13. Engine turn: `POST /tick` → `agent_adapter.tick` (116-151): if sleeping, only the learning gate; else `learning_gate.on_tick(False)` then `engine.send_tick(perception, call_budget)` (`pinned_boot.py:175-270`): re-fetch the registry with `force=LOCAL_TOOL_SCHEMAS` (194-205), start session-log/memory-stream turns under a per-engine lock (232-260), `coordinator.handle_input(perception, thread_id, RequestOptions(max_turns=call_budget))` (253-259). Each tool the model calls is a `ToolDef` whose handler POSTs `/agents/{id}/tool_calls` (`world_tools.py:180-216`); a 4xx is returned as `"Error: …"` text and written as a `correction` learning signal (198-211).
14. World side: `POST /agents/{id}/tool_calls` (2819-2837) → `registry.execute` → events on the bus → `persist` (3262-3297) → `TraceStore.record_*` (wiring 749-1043). Memories arrive via `POST /agents/{id}/memories` → `agent.memory.recorded` → `record_memory_if_new` (2839-2876, wiring 845-889). Inference cost arrives from the broker via `POST /ledger/debit` → `ledger.burn_for_inference` (2892-2915), which is what `SpendGovernor`/`GenesisMetrics` read (2033, 2086).
15. `run_life_ticks` (4565-4686): every tick `mark_the_broke_dormant` (4591, 4911-4958); hourly aging/coming-of-age/dormancy-death (4593-4612); whenever `game_day != last_life_day_processed` (4614): daily health rolls, `run_daily_upkeep` (4660), `charge_the_day_tax` (4674), `reconcile_wages` (4675), `expire_intentions` (4679), `assert_the_day_was_charged` (4684).
16. Shutdown (5271-5324): watchdog stop, groundskeeper stop, `drain_learning` (POST /learn to every engine), supervisor shutdown, uvicorn exit, final checkpoint.
17. Report: `world/run_report.py` runs as a separate process reading `/world/state`, `/world/utterances`, `/world/authored`, `/world/vitals`, `/world/refusals`, `/agents`, `/world/engines`, `/world/preflight`, `/tools/registry` off the live world, `llm_calls` and `events` off the trace db, and each engine's `session_log.jsonl` (775-1337).

### 2.3 Which world mode runs use, and what each mounts (Q1 / ROADMAP T3)

Every run is `--world-mode embedded` (`runseries.sh:88`, `start-genesis.sh:44`). The embedded app (2102-2921) mounts: `/admin/*` (2147-2183, `map_service=None` at 2176), the agents/location/governance/family/skills/workshop routers (2188-2225, all with `map_service=None`), `/health`, `/tools/registry(?agent_id)`, `/world/preflight`, `/world/vitals`, `/world/utterances`, `/world/conversations`, `/world/activity`, `/world/authored`, `/world/refusals`, `/tools/registry/version`, `/world/engines`, `POST /agents/{id}/tool_calls`, `POST /agents/{id}/memories`, `/ledger/payer/{id}`, `POST /ledger/debit`, `/metrics`.

`world/main.py` mounts the same viewer routers **plus** `social_api`, `agent_viewer_api`, `/ws/events`, `/crime/*`, `/research/control-businesses`, `/ledger/burn`, `/admin/tick`, `POST /tools/{tool_name}` — but **no** `POST /agents/{id}/tool_calls`, no `/agents/{id}/memories`, and its `/tools/registry` (346-363) ignores `agent_id`. So `--world-mode external` cannot host an engine (an engine would 404 on its first tool call), the "real" server has no continuous tick loop (ticks are single-stepped by `POST /admin/tick`, 564-585) and no supervisor.

**"Four contracts" today**, unchanged from ROADMAP §1.2 RC-3 in shape: (1) engine `world_tools.py` speaks `GET /tools/registry?agent_id`, `POST /agents/{id}/tool_calls`, `POST /agents/{id}/memories`, `GET /tools/registry/version`; (2) the embedded app serves exactly those plus ledger + admin + viewers; (3) `main.py` serves `POST /tools/{tool_name}` + ws + crime + agent-viewer + social and lacks (1); (4) the console's `/admin/*` is now served identically by both (admin_api is shared — that part of T3 is done). What is still not collapsed: `main.py` is not an engine host, the embedded app is not demoted to a fixture, `TraceStore.record_event`/`bind_event_bus` and `rewire_charter_event_sink` are only wired in `build_world_server`, a real `MapService` is only wired in `main.py`, and `CrimeEngine` exists only in `main.py`. T3's gate ("a rung-1 run through `world/main.py` writes rows to agents, events, utterances, transactions, memories, relationships and skills") is not met: a live run writes `agents` (seed), `businesses` (seed), `utterances`, `transactions`, `memories`, `skills`, `tools_registry`; never `events` (world side), `relationships`, `organizations`, `petitions`, `cases`, `buildings`; `world_snapshots` only via a mirror that writes to an in-memory db (CORE-14).

### 2.4 Time (Q2)

- Tick = 10 game minutes, 144/day, day 06:00-22:00 = 96 day ticks, 48 night ticks (`clock.py:19-27`). `DAWN_TICK = 36` (315). `--start-at-dawn` only sets the initial `tick_index` (3092); the first tick the runner executes is 37 (06:10).
- Real seconds per tick: **the configured `--tick-interval-s` is not applied** (CORE-05). Wall time per tick = the slowest due agent's turn (a `gather`, 4477) bounded by `tick_timeout_s = 120` (574). Run 23 took ~49 wall minutes for ticks 37→83 (report header 2:29 PM → 3:18 PM, 47 ticks) ≈ **63 s/tick**, four times the nominal 15 s, and a game day would be 144 × 63 s ≈ 2.5 h wall clock.
- Day boundary: `is_day_boundary(tick)` is true at tick 144, 288, … (`clock.py:75-82`); the engine writes the checkpoint and `system.checkpoint.written` at that tick (`engine.py:163-173`) before the runner's dispatch; the runner's day jobs key on `game_day` changing (4614) — which is the **first tick of the run**, not a boundary (CORE-07). Life ticks: hourly aging/dormancy-death (4593), daily health/death (4614-4641), upkeep (4660), head tax at `policy.tax_rate_bps` (default 0, `charter/models.py:260`) (4674), wage accrual (4675), intention expiry (4679).
- Night: the runner still dispatches every due agent with `is_sleeping=True` (4342); the engine short-circuits to the learning gate, which fires one batch at the sleep-onset edge (`learning_gate.py:185-201`, `agent_adapter.py:118-129`). There is no burst-through-night in the runner; `PacingMode.BURST` exists (`pacing.py:31-33`, settable via `PUT /admin/control/tick-speed`, `admin_api.py:1222-1228`) but the runner's loop never consults the mode. Continuing actions keep repeating through the night because `is_working` is checked before `is_sleeping` (4335-4342).
- Why no run has crossed a day: runs start at tick 36 and every series run spends its cap ($2-$10) by tick 49-93 (run-23 report table) — at ~$0.014/agent-tick × 20 agents ≈ $0.28/tick, a full 96-tick day is ≈ $27 for twenty residents, i.e. the cap is 3-5× too small for one day at today's per-tick cost, and wall clock at 63 s/tick would be 1.7 h just for the daytime. (Older non-series runs did cross days — `docs/RUN_REPORT.md` shows tick 1830/day 12 on 27 Aug, `docs/TEST_RUN.md` Run 4 tick 933/day 6 — but before upkeep/tax/dormancy were wired, so the docs' claim "no run has crossed a day with the day jobs live" is the accurate one.)

### 2.5 Scheduling (Q3)

- Cadence: `SchedulerConfig(target_fraction=0.3)` → `cadence_ticks = round(1/0.3) = 3` (`scheduler.py:52-58`); offsets 1..3 round-robin (131-136); `due_agents` = `next_decision_tick <= tick or interrupted` (154-162); `mark_decided` reschedules `tick + cadence` and clears the interrupt (164-173).
- Interrupts: `PerceptionSystem.attach` subscribes to `"*"` and interrupts every id in `target_agent_id`/`involved_agent_ids`/`witness_agent_ids` (`perception.py:51-69, 274-286`). `say` puts speaker + addressees in `involved_agent_ids` (`social.py:863`), so a conversation wakes its addressees on the very next tick — that is why the 17-person bakery ran 98 turns. Nothing rate-limits interrupts.
- `keep_doing` suppression: `is_working` → world-side repetition instead of a model call (4335-4338); `wake_if_working` (`state.py:1099-1109`) ends it when the resident is named in `say` (`social.py:951`); the reason is delivered as the first perception line (4393).
- Timeout: `asyncio.wait_for(..., 120)` (4447-4457) plus an httpx timeout of the same 120 s; the engine keeps running the abandoned turn (it holds its own `_turn_lock`, `pinned_boot.py:232`), pays for every call, and its late tool calls still land in the world against a perception the runner has already moved past. Run 23: 10 such turns.
- `gather` of all due agents: one call per due agent is the pacing assumption (4092-4096), but a turn is 2-12 model calls (`call_budget.py:46-47`) plus the post-turn summariser (`purpose.py` docstring: "close to one housekeeping call per thought"), so the true request rate is ~5-13× the formula.
- Back-pressure at the cap: the broker refuses (402/`SpendCapExceededError`), the engine's turn raises → `/tick` 500 → `succeeded=False` → `barren_ticks` (4490-4495) → after one barren tick the runner probes `/admin/spend` and stops on `state == "halted"` (390-427, 5186-5190). Only dispatch-level refusals count; a turn whose calls were all refused but whose `/tick` returned 200 is "success".

### 2.6 Perception text (Q4) — exact composition, in order (`retrieval_depth.py:926-1155`)

1. `It is game-day D, HH:MM (day|night).` (213-218)
2. `stopped_working` reason, if any (966-967; popped at 4393).
3. Intention line or its outcome, one of the two (433-460).
4. `Said to you, and not yet answered:` last 3 directed remarks from the hearing window with a "they are/are not standing with you" line (481-544).
5. `Still waiting on an answer from you:` up to 3 open questions from `WorldState.open_questions` (36-tick TTL) with "N ticks ago" and "`say` is how an answer reaches them" (547-602; dedup against 4 at 1007-1010).
6. `You asked, and have not been answered:` from `awaiting_reply` (1031-1039).
7. Standing brief (297-373): `You are NAME, the town's TRADE.` / `You are at X. Your work is at Y.` / food line (three variants, the zero-food one is 4 lines of explanation) / `You have N sim in hand.` / `Your household: …` / `The rest of the town: … These are the only other people in it.` (**the whole roster every tick**).
8. Volatile line (231-294): `Energy: $x.xx in your wallet. Your last turn cost $… At this morning's rate that is about N game-days. Money: N sim.` (+ "Owed today" — never produced, see CORE-19; + "ran out after N steps" once).
9. Silent-turn report (`state.py:1548-1585`).
10. Conversation-open block or convening hint (605-644, `state.py:1587-1641`).
11. `With you (N present, and nobody else):` bios for up to `max_visible_agent_bios` (0 at depth 0, 6 at depth 1.0; `--retrieval-depth 1.0` in `start-genesis.sh:47`, default 0.0 in `runseries.sh`), each with any posted offer (376-430).
12. `Priced where you stand:` (463-478) from `describe_prices`.
13. `Recently:` speech first (newest first), then other events, capped at 3-20 by depth (845-883); each event either a rendered sentence (utterance, conversation opened, revived, dormant) or a raw `[type, marker] {payload}` dict (841-842).
14. Relationship notes — only above depth 0.5 **and the runner never passes any** (926-948 vs 4350-4442) → dead.
15. Skill playbook with the caret (895-923); authoring hint; gap notice.
16. Closing: `Nothing you have thought or written above has happened yet…` (1149-1153).

Measured size: median ≈ 700 chars, longest ≈ 1,175 (comment at 3849-3850) for 20 residents; the roster line is O(N) and would be ~5-6 KB alone at 250. Per-turn input is 15-20 K tokens (run 23) — the perception is a small part; the rest is persona (STABLE section), 76 tool schemas (~5.8 K tokens, `docs/TEST_RUN.md` "Where it is NOT"), and thread history up to the 0.12 compaction line (~20 K).

### 2.7 Trace/record (Q5)

`TraceStore` is bound only by `attach_persistence` (3202-3300): `record_transaction_if_new` (wiring 749-765, on `economy.wallet.credited`), `record_utterance_if_new` (767-843, with `addressed_agent_ids`), `record_skill_if_new` (891-966), `record_invented_tool_if_new` (968-1043), `record_memory_if_new` (845-889). **`record_event` is not called** (3216-3223), so `events` has only broker rows. Halting: three consecutive rejected writes → `request_stop()` (3277-3295) → the run stops at the end of the current tick. `run_report.py` reads Prices/Stakes/Wages exclusively from `events` (1045-1083) with the wrong type name for prices (`business.price.posted` at 374/1070 vs `economy.price.posted` emitted at `commerce.py:642`) — hence "Nobody posted a price" over 12 posts (CORE-03).

### 2.8 Checkpoint/resume (Q6)

Registered components: `clock`, `scheduler`, `locations`, `movement` (engine), `ledger`, `world_state` (all `OWNED_STATE_FIELDS`: agents, households, businesses, resource_zones, storages, plots, buildings, skills, prayer_requests, crime_cases, custom_tools, subpoenas, tool_invocations, agent_tiles, tile_owners, wills, skill_sources, skill_reps, skill_definitions, skill_progress, turn_ticks, continuing, wake_reasons, price_board, wage_promises, loans, arrears, intentions, tool_licences, tool_owners, tool_copies, police, confined — `state.py:214-268` — plus inventory), `world_locations`, `runner_counters`, `charter` (store + amendment proposals), `records` (utterances, tool_refusals). Written at `tick % 144 == 0` and at shutdown (5317-5324) to `data/kasukabe-checkpoints/checkpoint_NNNNNNNNNN.json.gz` (1811-1906, fsync'd). Resume = `latest()` unless `--no-resume` (3188-3195). Not covered: see CORE-08.

### 2.9 Supervision (Q7)

`ProcessSupervisor` (1001-1203): `create_subprocess_exec(python -m src.sim.sim_main)` with merged env, stdout drained into a 50-line tail (1036-1074), `status()` downgrades to FAILED only on a non-None returncode (1180-1190), `restart` = stop + start from the remembered spec (1160-1178). Watchdog (846-960) polls every 10 s and restarts FAILED units with backoff, max 5. Memory: the constant is 550 MB/engine Linux-under-load, 39-88 MB measured on macOS/Windows (`memory_budget.py:73-86` own numbering; `docs/TEST_RUN.md` "Per-engine memory"); `check()` (own 196-219) refuses a run whose `N × mb_per_engine` exceeds free − 512 MB. What limits ~20 on the dev box: an 8 GB laptop and the measured ~48-88 MB/engine plus the world+broker+viewer (≈ 1.5-2 GB usable → 20-30 engines), one uvicorn process + one SQLite per engine, ports 9100+. What limits 250 on metal: with 384 GB RAM even 550 MB × 250 = 137 GB fits; the binding limits become the single-threaded world event loop (every tool call, event fan-out ×N subscribers, per-agent registry JSON-schema regeneration per tick, perception renders, SQLite writes under one lock), the broker's 600 RPM against 250×0.3×(2-12) calls/tick, the 2,000-event ring buffer, and the `gather`-bound tick.

### 2.10 Observability (Q8)

Preflight (`preflight.py:303-681`): 13 pre-boot checks + live `Every engine booted`/`World is ticking`/`Authoring tools reachable`/`Broker is writing`/`Every engine is writing a session log` served at `/world/preflight` (2305-2364). Metrics: `/metrics` (2917-2919) = spend, burn rate, population, births, deaths, elections, crimes, businesses, uptime. `/world/engines` adds timed-out turns (3718-3726). The observatory (`app/observatory/app.py`, :8500) reads `/world/refusals` and `/world/engines`; nothing in the run scripts starts it and run 23 reports it unreachable. Logs are the only place the recorder's drop counter, the watchdog's decisions, the groundskeeper's sweeps and the pacing decision appear.

---

## 3. Findings

Severity: P0 blocks the target / corrupts data or money; P1 major gap; P2 defect; P3 polish. Layer: seam / world / registry / frontend / broker / engine-finding.

### CORE-01 — `--tick-interval-s` is dead: the runner's loop never sleeps, so the town runs at "as fast as the slowest turn"
- **P1 · world · S** · `run_genesis.py:5171-5209`, `4028-4039`, `587`, `pacing.py:77-89`
- Evidence: `run_continuous` calls `run_tick()` then `await asyncio.sleep(0)` (5209). `apply_tick_pacing` writes `self.tick_engine.pacing.config.continuous_interval_s` (4038), but `PacingController.run_continuous` — the only reader of that value (`pacing.py:86-87`) — is never invoked (grep: no `pacing.run_` in `run_genesis.py`). The config docstring calls the interval "THE request-rate control" (582-587) and `runseries.sh:94` passes `--tick-interval-s 15`. `TestTickPacing` (`test_run_genesis.py:1356-1421`) only tests `resolve_tick_interval`'s arithmetic, never that a tick waits.
- Why it matters: the only pacing is turn latency; a fast tick (everyone asleep, or everyone under `keep_doing`) runs at event-loop speed, which is exactly the "burned an entire game day in 0.65 s" failure the `wait_for_engines_ready` docstring records (4149-4154). It also means the RPM math in `resolve_tick_interval` has never been in force on any run.
- Fix: in `run_continuous`, after `run_tick()`, `await asyncio.sleep(max(0, interval - elapsed))` where `interval = self.tick_engine.pacing.config.continuous_interval_s`; or drive the loop through `PacingController.run_continuous(tick_fn=self.run_tick)`. Add a test that a mock run with `tick_interval_s=0.2` takes ≥ 0.2 s × ticks.

### CORE-02 — Charter events never reach the bus in a live run (no `rewire_charter_event_sink` in the embedded root)
- **P1 · world · S** · `wiring.py:284-306, 1443-1445` vs `run_genesis.py:3070-3126`; `bootstrap.py:298`; `charter/engine.py:36-60`
- Evidence: `build_world()` constructs the charter with `event_sink=None` (`bootstrap.py:298` → `create_genesis_charter_engine(genesis_tick)`), leaving every sub-engine on `noop_sink`. Only `build_world_server` re-points them (wiring 1443). `GenesisRunner.boot` never does (grep `rewire_charter_event_sink` in run_genesis.py: no hit).
- Consequences: no `politics.*` event for elections, amendments, patents, organizations, complaints or land petitions ever exists in a live run → `GenesisMetrics.elections` can never increment (2010-2012), `/admin/alerts` (`admin_api.py:1138-1157`) can never show an amendment or regime change, the perception interrupt for "you were named in a warrant/amendment" never fires, the trace has no politics rows, and `/metrics` reports `elections: 0` forever. The `schedule_election(0)` at 3485 fires silently.
- Fix: after `build_world()` in `boot()`, `rewire_charter_event_sink(self.world_state.charter, lambda e: self.world_state.event_bus.publish(charter_event_to_world_event(e, self.world_state.tick())))` (both already importable). Test: seed, schedule an election, assert a `politics.election.*` event lands in `event_bus.recent()`.

### CORE-03 — The run report's Prices / Stakes / Wages sections cannot ever be non-empty in a live run
- **P1 · world · S** · `run_report.py:362-389, 392-434, 437-479, 1045-1083`; `run_genesis.py:3216-3223, 4730`; `commerce.py:642, 691`
- Evidence: (a) the embedded run does not write world `events` rows at all (3216-3223: "Deliberately NOT record_event()"), yet `_events()` reads `events` (1046-1051) for `business.price.posted`, `economy.upkeep.charged`, `economy.head_tax.charged`, `economy.energy.bought`, `agent.dormancy.entered`, `business.wage.set`, `business.wage.paid`; (b) the price event is actually named `economy.price.posted` (`commerce.py:642`; `stakes_report.py:189` uses the right name); (c) `charge_the_day_tax` passes `event_sink=None` (4730) so head-tax events are never even published; (d) the section text then asserts "**Nobody posted a price** … every run before this one produced the same zero" over a run with 12 posts. The footer (1332-1336) still says "the world's event bus is not bound to it in a genesis run", which is half-true and stale.
- Fix: (1) record a filtered allowlist of world events (`economy.*`, `business.*`, `politics.*`, `health.*`, `crime.*`, `agent.dormancy.*`, `agent.utterance.spoken` metadata, `system.*`) in `persist`, explicitly excluding per-tile `agent.moved` — that is the unbounded 21 GB risk the comment fears, not the rest; (2) correct the event names in `run_report.py` and make `_events` fall back to `/world/*` live state; (3) hand `charge_the_day_tax` a sink that publishes `LedgerEvent`s on the bus. Test: a mock run that posts a price then generates the report must print the price.

### CORE-04 — Day-boundary jobs run on the first tick of every run (and only then, so far)
- **P2 · world · S** · `run_genesis.py:1741, 4614-4686`
- Evidence: `RunnerCounters.last_life_day_processed = -1` (1741); the guard is `timestamp.game_day != last_life_day_processed` (4614). On the first tick (37 under `--start-at-dawn`) `game_day` is 0 ≠ −1, so `run_daily_upkeep` (4660: 90 sim/member/day, `upkeep.py:81-86`), `charge_the_day_tax` (4674, at 0 bps → charges nothing), `reconcile_wages` and `expire_intentions` all execute at 06:10 of day 0 — before anyone has earned. Inferred from code; verify against a run-23 ledger: look for `reason='daily_upkeep'` transactions at tick 37.
- Why it matters: SYSTEM_REVIEW.md §3 and the run reports state "upkeep and head tax have never fired in a live run"; per the code they fire every run, at the start, invisibly (see CORE-03 for why the report cannot show it), and they will fire a second time at tick 144. Either the docs are wrong or the day-0 charge is unintended; either way the semantics ("charged for the day just completed" vs "charged at the start of the day") are undocumented, and the hourly block likewise ages everybody by 6.17 h on the first tick when starting at dawn (4573, 4593-4594).
- Fix: initialise `last_life_day_processed` to the boot day (`timestamp_for(initial_tick).game_day`) and `last_life_hour_processed` to the boot hour; charge on `is_day_boundary` (day just completed), and say so in the volatile line ("Upkeep for day N was charged at midnight").

### CORE-05 — The pre-halt learning drain cannot fire on a live run: `run_continuous` never calls `drain_before_halt`
- **P1 · world · S** · `run_genesis.py:5171-5209` vs `5211-5231`, `5240-5269`
- Evidence: `drain_before_halt()` is called only from `run_burst` (5228). `main_async` runs `run_continuous` unless `--burst-ticks` (5479-5482). The run-23 operator note attributes the zero firings to the 10-cent reserve being "too narrow"; the actual cause is that the call site is on the test-only path. `test_the_drain_is_attempted_before_the_cap_halts_dispatch` (`test_the_learning_drain_is_honest.py:63-77`) asserts only that the strings `drain_learning` and `DRAIN_RESERVE`/`drain_before_halt` appear in the class source — it cannot fail for this bug.
- Fix: call `await self.drain_before_halt()` in `run_continuous` right after `run_tick()`; make the reserve a fraction of the cap (e.g. 5 %, min 10 ¢); test with a mock gateway whose `drain_learning` records a call, driving spend across the reserve in `run_continuous` with `max_game_days`.

### CORE-06 — Population dials from the console never reach the life tick
- **P2 · world · S** · `run_genesis.py:3047-3050, 4574-4576, 4607, 4624-4630, 4990`
- Evidence: `run_life_ticks` builds a fresh `PopulationDials(target_band_min=…, target_band_max=…)` every tick (4574-4576) and passes that to `check_dormancy_death`, `check_terminal_death`, `check_chronic_death`, `check_elderly_death` and `run_daily_upkeep`. `self.population_dials` (3047), which `PUT /admin/god/population-dials` mutates (`admin_api.py:1096-1104` → 4229), is read only by `grant_blessing`. The `__init__` comment (3040-3046) claims the opposite.
- Fix: replace the local with `self.population_dials`. Test: set `death_hazard_multiplier=0` via the admin route and assert no death roll can succeed.

### CORE-07 — Resume leaves the run unpaced and skips the pacing probe
- **P2 · world · S** · `run_genesis.py:3085, 3188-3198, 3487`
- Evidence: `apply_tick_pacing()` is called only inside `seed_genesis` (3487); the resume branch (3190-3195) restores and returns. (With CORE-01 the interval is dead anyway, but once CORE-01 is fixed this becomes a real regression on every resume.)
- Fix: call `apply_tick_pacing()` after the if/else in `boot()`.

### CORE-08 — Checkpoint incompleteness: hunger, head-tax debts, conversations, day-job ledger, perception delivery state
- **P1 · world · M** · `run_genesis.py:3014-3018, 3003, 3007, 3106-3126`; `head_tax.py:355-361`; `state.py:214-268, 429`; `perception.py:272`
- Not checkpointed: `hunger_by_agent` (3018 — the starvation ladder resets to fed on resume), `head_tax_debts` (3014 — `HeadTaxDebtStore` has `snapshot/restore` at `head_tax.py:355-361` but is never registered; note `arrears` in world state mirrors only the amounts, not the events), `day_jobs_ran` (3003 — `assert_the_day_was_charged` reads it), `budget_hit_last_turn`, `world_state.conversations` (`state.py:429` is outside `OWNED_STATE_FIELDS` and outside `RecordCheckpointAdapter` 1632-1669, so a resumed run has utterances whose `conversation_id`s point at nothing: `/world/conversations` empty, `conversation_here` None, `conversation_pointer` None), `gap_feedback` (bootstrap 346), `PerceptionSystem._last_perceived` (perception 272 — after resume every buffered directed remark is "never delivered" again; the buffer is empty after restart so in practice remarks are simply lost), the engine-side `SimLearningGate` state and the runner's `barren_ticks/timed_out_ticks` (fine to lose). Also `timestamp_for(latest.tick_index)` vs `world_state.current_tick = latest.tick_index` (3195) — consistent.
- Why it matters: the target needs multi-day runs on spot hardware; the roadmap's "bit-exact resume" claim is currently false for the economy's stakes.
- Fix: register adapters for `hunger_by_agent`, `head_tax_debts`, `day_jobs_ran`; add `conversations` to `RecordCheckpointAdapter`; persist `_last_perceived` via the `locations`-style snapshot on `PerceptionSystem`; add a round-trip test that a run with an open conversation, an unpaid day and a tax debt resumes with all three.

### CORE-09 — The 2,000-event ring buffer is the delivery mechanism for speech, and walking floods it
- **P1 · world · S** · `events.py:136-138`, `movement.py:505-506, 793-826`, `perception.py:300-395`, `admin_api.py:740, 778, 1134`, `location_api.py:573`
- Evidence: `PerceptionSystem.build_perception` reads `event_bus.recent()` (331) to compute "said to you, never delivered" (369-371, "NO age limit") and "still unanswered" (377-381, 30 ticks). `MovementSystem.advance` emits **one `agent.moved` per tile crossed**, up to 40 per resident per tick (795-816 with `STREET_TILES_PER_TICK = 40`). Twenty walking residents can push 800 events per tick into a 2,000-entry deque, so the buffer spans ~2.5 ticks and both guarantees are void whenever people walk. Each of those events also fans out to `persist` (five `record_*` checks each) and to the interrupt policy. Admin projections (broadcasts, honors, disasters, alerts) and `location_api.recent_events_at` read the same buffer and silently forget.
- Fix: (a) size the buffer by policy (e.g. `EventBus(buffer_size=max(20_000, 200 × agents))`) and/or keep a per-domain ring; (b) have `MovementSystem.advance` emit one `agent.moved` per resident per tick carrying `path=[…tiles…]` and `arrived`, so walking is O(residents) not O(tiles); (c) make `WorldState.open_questions` (utterance-backed, capped) the single source of directed-speech obligations and drop perception's parallel `never_delivered/still_unanswered` scan (see CORE-16). Test: 20 residents walk 40 tiles for 3 ticks, a remark to a fourth resident at tick t must still be delivered at t+3.

### CORE-10 — Dead and dormant residents keep being dispatched
- **P2 · world · S** · `run_genesis.py:4322-4345, 5012-5031, 4911-4958`; `scheduler.py:139`
- Evidence: `dispatch_due_agents.one()` checks neither `agent.alive` nor `dormancy.is_dormant`; `apply_death` never calls `scheduler.unregister_agent` (grep: no production caller of `unregister_agent`); `mark_the_broke_dormant` sets the flag and pops `continuing` but leaves the schedule. A dormant resident's engine is still POSTed every third tick, calls the broker, gets 402, raises, returns 500 → counts as a dispatch failure; a dead resident's engine keeps running and paying for the summariser if it has money. The engine also keeps consolidating ("a dead agent learns").
- Fix: skip (and do not `note_turn`) residents that are dead or dormant; `unregister_agent` on death; stop the engine process on death (supervisor `stop`) and on dormancy suspend it (or send `is_sleeping=True`). Test: mark an agent dormant in a mock run and assert the gateway is never called for it.

### CORE-11 — Timed-out turns are cancelled on the runner side only; the engine finishes them against a stale world and the next tick queues behind
- **P2 · world+seam · M** · `run_genesis.py:4447-4474, 574`; `pinned_boot.py:222-259`; `agent_adapter.py:116-151`
- Evidence: `asyncio.wait_for` cancels the awaiting coroutine (the HTTP request is closed by httpx at the same 120 s), but the engine's `handle_input` continues under `_turn_lock` (232-260); the runner marks the agent decided (4474) and three ticks later POSTs again — that POST blocks on the lock until the old turn finishes, then runs a turn on a perception that is already old. Money spent, actions taken late, `timed_out_ticks` incremented (4468) with no way to attribute the late tool calls. Run 23: 10 turns.
- Fix: make turns asynchronous — the runner keeps an `in_flight: set[agent_id]`, never re-dispatches an in-flight agent, and never cancels; the engine answers `409` if a tick arrives while one is running (seam change in `agent_adapter.py`, allowed under Law 1). Combine with a per-day turn budget rather than a per-tick wall-clock kill. Test: a gateway that sleeps 2× the timeout must not receive a second `/tick` while the first is pending.

### CORE-12 — RPM/pacing arithmetic counts one call per due agent; a turn makes 2-12 plus a summariser
- **P2 · world · S** · `run_genesis.py:4087-4141`; `call_budget.py:46-47, 57, 69`; `broker/purpose.py` docstring
- Evidence: `safe = due_per_tick * 60 / capacity` (4125) treats a turn as one request. `budget_for` allows up to 12 model calls per tick; the post-turn learning signal is "close to one housekeeping call per thought". At 250 residents the real demand at cadence 3 is 83 turns/tick × (≈4 + 1) ≈ 415 calls per tick against 600 RPM — i.e. ≥ 40 s/tick just to stay inside quota, and 2.5 h per game day at best.
- Fix: derive the interval from `due_per_tick × (expected_calls_per_turn + 1)` with `expected_calls_per_turn` measured from the last N turns' `steps_used` (already reported by the engine, 1361-1362); publish the number on `/metrics`.

### CORE-13 — A re-run against an existing trace store poisons the recorder and can halt the run after three remarks
- **P1 · world · S** · `run_genesis.py:3395-3407, 3489-3496`; `wiring.py:655-711, 767-789`
- Evidence: `seed_agent` adds to `known_agent_ids` only after a successful insert (708). On a re-run against an existing db every insert raises `UNIQUE constraint failed: agents.slug`, which `seed_genesis` swallows and logs as benign ("That is a re-run, not a fault", 3399-3406). Nothing else ever populates `known_agent_ids` (grep: single `.add` at wiring 708). `record_utterance_if_new` then treats every speaker as "never seeded" and calls `record_write_failure` (781-789); three in a row → `is_halted` → `request_stop()` (3287-3295). `build_transaction_row` silently nulls both agent columns (506-507), so money rows lose their parties; memories are dropped silently (867-868). `runseries.sh:47` deletes the db each iteration, which is why the series never hit this; `start-genesis.sh` (persistent `/data/kasukabe.db`) and preflight check 18 ("Broker is writing to this trace store", expects rows) both assume reuse.
- Fix: on `IntegrityError` for an existing slug, `SELECT` the row and add the id to `known_agent_ids`/`seeded_agent_ids` (or upsert); test: seed twice into one store, then record an utterance, assert it lands.

### CORE-14 — The daily checkpoint "mirror" writes to an in-memory database
- **P2 · world · S** · `run_genesis.py:1909-1968, 3236-3244`
- Evidence: `mirror_checkpoint_to_shared_store` calls `get_client()` with no config (1924). `attach_persistence` documents that this path "falls through to `SupabaseConfig.from_env()`, whose `sqlite_path` defaults to `":memory:"`" and deliberately builds its own config (3236-3249). So every day-boundary `world_snapshots` insert in a live run goes into a fresh in-memory db and is closed (1966), logging success. The `TRACE_SQLITE_PATH` env var is only honoured by the broker and by `attach_persistence`.
- Fix: reuse `self.trace_store.client` (or the same `SupabaseConfig` override) for the mirror; assert in a test that `world_snapshots` in the on-disk db has a row after a day boundary.

### CORE-15 — Two disagreeing "unanswered question" mechanisms, and questions outlive the asker's presence
- **P2 · world · S** · `perception.py:232, 333-344, 353-381`; `state.py:140, 1643-1722, 1724-1796`; `retrieval_depth.py:481-544, 547-602, 856-863`
- Evidence: perception's `answered_at` (335-344) recognises an answer only when `listener_agent_id` equals the asker — it ignores `addressed_agent_ids` — while `WorldState.open_questions` reads both (1656-1668). TTLs differ (30 vs 36 ticks). Neither checks whether the asker is still in the room, and `format_addressed_to_you` then appends "They are no longer standing with you; `say` only reaches someone in the same place" (539-543) — so the prompt carries, for up to 36 ticks, a debt the resident is told it cannot pay, and `format_open_questions` (601) adds "`say` is how an answer reaches them" without the caveat. This is the run-23 "unanswered questions persist for 30+ ticks after the asker left" symptom. `skip_addressed` (856-863) also filters on `listener_agent_id` only, so a multi-addressee remark is printed twice.
- Fix: one mechanism — keep `WorldState.open_questions` (it is checkpointed via `utterances`), delete perception's `never_delivered/still_unanswered` branches (keep `in_window`), and (a) drop a question when the asker leaves the place unless it was in a convened conversation, or render it as "X asked you before leaving: …" without the reply instruction; (b) read `addressees_of()` everywhere. Test: A asks B, A walks away, B's next perception must not say B can answer; B answering A inside a remark addressed to C must clear the question.

### CORE-16 — What a resident is *not* told (perception gaps for thriving)
- **P1 · world · M** · `run_genesis.py:3571-3658, 3513-3553, 4864-4909`; `retrieval_depth.py:276-281, 886-892, 1108-1111`
- Missing from every tick: (1) inventory beyond a food count (`standing_brief` sums `EDIBLE` only, 3590-3594; timber/ore/flour/tools invisible); (2) storage holdings and the business's own till/stock/employees for owners and staff; (3) obligations: `debts_of`/`tax_arrears_for`/`wage_promises_for` exist (`state.py:867-996`) and are rendered nowhere; the volatile line's `upkeep_due_sim`/`head_tax_due_sim` keys (276-281) are never produced by `wallet_line` (3537-3553) → dead branch; (4) the commitment under `keep_doing` (only its *ending* is rendered, 4393) and the journey in progress / ETA (`world.movement.journey_for` never rendered; `look_around` during a walk shows the origin, run 23); (5) goals beyond one free-text `intention`; (6) per-action prices before acting (`quote_action_cost` is a tool, never volunteered); (7) relationship notes — never passed (dead at 1108-1111); (8) time-of-day plan / what the household expects; (9) the town's institutional state (who governs, tax rate, open tenders, elections due). Meanwhile the brief repeats the whole roster every tick (363-371) — O(N) tokens for a fact that belongs in the STABLE persona section.
- Fix: a structured "ledger of self" block computed world-side each tick (holdings incl. non-food, storages, business till/stock, debts owed/owing, wages owed, arrears, active commitment + repetitions, journey ETA, today's upkeep/tax), and move the roster to the persona/STABLE layer. Keep the no-advice rule. Test: assert each field renders from a seeded world.

### CORE-17 — Night is neither cheap nor consolidated: no burst, continuing actions work through it, interrupts are discarded
- **P2 · world · S/M** · `run_genesis.py:4335-4345, 4474`; `pacing.py:31-33`; `agent_adapter.py:118-129`; `learning_gate.py:185-201`
- Evidence: (a) a sleeping resident still costs a round trip and a `mark_agent_decided`, which clears any interrupt raised overnight (4474) — a remark or a dormancy notice at 23:00 is forgotten by 06:00; (b) `is_working` is evaluated before `is_sleeping`, so `keep_doing` repeats (and charges) through the night; (c) `BURST` mode exists but the runner never uses it, so 48 night ticks cost 48 × (max engine round trip) of wall clock. The learning batch runs once at sleep onset (correct), but with CORE-01 fixed a 15-s interval would make the night 12 minutes of nothing.
- Fix: at `not is_daytime` skip dispatch entirely except one `/tick(is_sleeping=True)` per resident at the sleep-onset edge (or a `POST /sleep`), suspend continuing actions at 22:00 (resume reason at dawn), do not clear interrupts overnight (deliver at dawn), and burst the 48 night ticks with a 0 interval while still running the hourly life ticks. That alone removes a third of the ticks from the wall clock and costs nothing.

### CORE-18 — The hung-engine half of the watchdog is dead on the process backend
- **P2 · world · S** · `run_genesis.py:875-888, 1080-1137, 1180-1197`, `570-573`
- Evidence: `needs_restart` treats a RUNNING unit as silent only if `now − last_health_check_at > 180 s` (883-887), but `ProcessSupervisor.start` sets `last_health_check_at=None` (1123) and nothing ever updates it (`status`/`health` at 1180-1197 do not). So only a process that *exits* is restarted; a wedged engine (the case the config docstring says "is still caught by the layer whose job that is", 570-573) is never restarted. `test_run_genesis_watchdog.py` exercises the silence path with a `MockSupervisor` that sets the timestamp, so it passes.
- Fix: have `ProcessSupervisor.health()` GET `/health` on the unit's port and stamp `last_health_check_at` on 200; call it from the watchdog poll.

### CORE-19 — Route collision: two `GET /world/state` definitions on the embedded app
- **P2 · world · S** · `run_genesis.py:2196-2204`; `agents_api.py` own 509-556; `location_api.py:737-739`
- Evidence: `build_agents_router` defines `/world/state` (returns `run_id`, population, tick) and `build_location_router` defines `/world/state` (returns clock/government/ledger). Both are included; FastAPI serves the first registered (agents_api). `run_report.py:757-772` and `run_doc_for` depend on `run_id` from the first; the governance-shaped view is unreachable on the live app. Silent shadowing, no test.
- Fix: rename the location one (`/world/summary`) and test both.

### CORE-20 — `NamespaceAgentGateway` does not implement the gateway contract; the namespace backend would crash on first dispatch
- **P3 (dead backend) · world · S** · `run_genesis.py:748-761, 1287-1299, 4447-4455`; `namespace_supervisor.py` own 199-218
- Evidence: the runner calls `self.gateway.send_tick(agent_id, tick=…, is_sleeping=…, perception=…, call_budget=…)` (4448-4455) but `NamespaceAgentGateway` only defines `tick(agent_id, perception_text)` (760-761) → `AttributeError` on the first due agent. `NamespaceSupervisor.restart` rebuilds the spec with `cdt_key=""` and an env of only `AGENT_ID` (own 210-218), losing the broker URL/key. The backend is documented as unable to host an engine anyway (518-535).
- Fix: either finish it (host subprocess in the engine venv, as `docs/TEST_RUN.md` "Namespace backend" specifies) or remove the option from `validate()`/CLI so nobody boots a run that dies at tick 1.

### CORE-21 — `world_state.locations` and `tick_engine.locations` are two indexes; `place()` at genesis writes both by hand
- **P3 · world · S** · `run_genesis.py:3425-3426`, `engine.py:76, 94`, `bootstrap.py:295`
- Two `LocationIndex` instances subscribed to the same bus; genesis placement writes both directly (no `agent.spawned` event is published in the embedded root, unlike `wiring.py:427-429`), so the trace/perception never see a spawn. Harmless today; a divergence would be invisible. Fix: publish `agent.spawned` at genesis and let both indexes react; or hand the engine `world_state.locations`.

### CORE-22 — Per-agent registry re-served and JSON schemas regenerated for every due agent every tick
- **P3 · world+seam · S** · `run_genesis.py:2277-2289`; `registry.py:163-212 (196, 208)`; `pinned_boot.py:194-205`; `world_tools.py:292-311`
- With `KASUKABE_LOCAL_TOOL_SCHEMAS=1` (default) every engine re-GETs the full registry on every tick with `force=True` and rebuilds every `ToolDef`; the world calls `params_model.model_json_schema()` for 76 tools per request. At 250 residents that is ~83 schema builds × 76 per tick on the world loop. Fix: cache `model_json_schema()` per spec and per (location, version); make the engine refresh only when `version` or its own location changed.

### CORE-23 — Tests that cannot fail (source-string assertions) on the exact paths that are broken
- **P2 · tests · S** · `test_the_learning_drain_is_honest.py:63-77`, `test_upkeep_is_wired.py` (every test), `test_tick_timeouts_agree.py:30-40`, `test_run_genesis.py:1356-1421`
- Each asserts substrings of `inspect.getsource(...)` ("`drain_before_halt` in source", "`run_daily_upkeep` in source", "`timeout_s` in call"). None exercises the loop, so CORE-01, CORE-04, CORE-05 and CORE-07 all pass the suite. Fix: replace with behavioural tests on a mock-mode `GenesisRunner` (`make_launched_runner` already exists in `test_run_genesis.py`).

### CORE-24 — Recorder statistics are invisible; no run manifest; no event schema version; no per-tick perception record
- **P1 (instrumentation) · world · M** · `wiring.py:620-632` (`TraceStore.stats()` has no route; grep: no caller in `run_genesis.py`/`main.py`), `agents_api.py` own 132 (`RUN_ID` minted at import, persisted nowhere), `checkpoint.py:46-53` (no run id, no genesis hash, no code version), `sqlite_schema.sql:380-391` (`events` has no `schema_version`), `run_genesis.py:4349-4445` (the rendered perception is not stored world-side; only the engine's `session_log.jsonl` and the broker's 64 KB-capped `request_body` hold it)
- Why it matters: "every claim traceable to a trace row, reproducible" is unreachable when the run's config, seed, genesis hash and git sha are only in shell history, the recorder's dropped-row count is only in a WARNING line, and a turn's input exists only in per-engine JSONL under `/tmp`.
- Fix: (1) write `run_manifest.json` (config, argv, env knobs, genesis sha256, git sha, engine sha, model ids, lane capacities, start time, `RUN_ID`) into the checkpoint dir and the trace (`world_snapshots` or a `runs` table) at boot; stamp `run_id` into every event/utterance/transaction row; (2) `GET /world/recorder` exposing `TraceStore.stats()` and add it to the report's instrument table and to preflight's live checks; (3) a `turns` table: `(run_id, tick, agent_id, is_sleeping, perception_text, call_budget, steps_used, budget_hit, succeeded, error, latency_ms)` written by `dispatch_due_agents`; (4) `schema_version` on `events` and a `CONVENTIONS.md` event catalogue test that every emitted type is listed.

### CORE-25 — Spend governor and broker cap are the same number, and the governor can only trip after the broker already refused
- **P3 · world · S** · `run_genesis.py:651-666, 2083-2096, 5178-5190`
- Both stop at `SPEND_CAP_USD`; the broker halts first, refuses every call (each still a round trip and a trace row), the engines' turns 500, one barren tick passes, then the probe stops the run. Fix: set the runner's cap a reserve below the broker's (this is also where CORE-05's drain belongs) and surface `spent/cap` on `/metrics`.

### CORE-26 — `agent_viewer_api` (the "research centrepiece" reasoning viewer) is not mounted on the host that runs
- **P2 · world · S** · `run_genesis.py:2188-2225` vs `main.py:186-200`
- The embedded app has `genesis`, `tick_engine` and a trace client (`self.trace_store.client`) — everything `AgentViewerDeps` needs — but does not include the router; `run_report.py:783-786` already notes the 404. Fix: include it with `trace=self.trace_store.client` when `trace_store` is not None.

### CORE-27 — `checkpoint_every_day` and `supabase_bucket` are inert; `world_mode="external"` is accepted but cannot work
- **P3 · world · S** · `run_genesis.py:549, 4286-4287 (`pass`), 589, 596-599, 4207`
- `checkpoint_every_day=False` changes nothing (the engine writes regardless); `--world-mode external` skips the embedded server and points engines at a host (`main.py`) that lacks their routes (§2.3). Fix: delete the dead knob; make `validate()` reject `external` until `main.py` serves the engine contract (or make it serve it — that is T3).

### CORE-28 — `_phrase_activity` and the activity feed use `listener_agent_id` only; `run_report.threads` reply detection likewise
- **P3 · world · S** · `run_genesis.py:2699-2705`, `run_report.py:565-573, 967-971`
- Multi-addressee remarks (172 of 229 in run 23) render as "speaking to everyone here" / no `→` marker. Fix: read `addressed_agent_ids`.

### CORE-29 — The learning corrector writes `source="engine"`; the pipeline's evolution step keys on `source == "user"`
- **P1 · engine-finding (cross-scope; verify with the learning reviewer) · S** · `engine/src/sim/world_corrections.py:75-76`; `engine/src/learning_centre/pipeline.py:460`
- Run 23 counts 70 corrections written; if `step11_skill_evolution`/`MEANINGFUL_USER_TYPES` still require `source == "user"` (ROADMAP §1.3 says it does), those signals are stored and never drive skill evolution. Either set `source="user"` in the seam (legal under Law 1) or write up the finding.

### CORE-30 — Broker purpose tagging: the engine still sends no `X-Kasukabe-Purpose`; classification is inferred broker-side
- **P3 · broker (already mitigated) · —** · `broker/purpose.py:1-60`; grep of `engine/src/sim` for the header: none
- Not a defect anymore, but the roadmap's T4 gate ("background share > 0 and matches sleep-onset edges") is now a broker inference, not a fact the engine asserts; the trace should record `purpose_source = header|inferred`.

### CORE-31 — Genesis placement puts everyone at `home:<household>`, which the map cannot resolve; `move` from there is an instant jump
- **P2 · world · M** · `run_genesis.py:2965-2974, 3421-3426`; `location_api.py:9-57` (the "two disconnected spaces" finding); run 23 ground truth ("`move` is an instant jump for residents with no known tile")
- The embedded root never places agents on a tile (`world.agent_tiles` empty until the first walk), so the first `move` teleports and the resident is not told which kind of move it got. Fix: at genesis resolve each household to a tile (the map has houses) and publish `agent.spawned` with `tile_x/tile_y`; make the `move` result say "instant" vs "walking, ETA N ticks"; render the ETA in perception (CORE-16).

### CORE-32 — `BARREN_TICKS_BEFORE_STOP` is measured in ticks whose wall length is unbounded
- **P3 · world · S** · `run_genesis.py:376-380, 5191-5202`
- "Twenty is roughly a game-hour at the default pacing" assumed 15 s ticks; at 63 s/tick it is 20 minutes of refusals, at 120 s (all timing out) 40 minutes. Fix: also stop on wall-clock (`barren_seconds`) or on the broker's `state`.

### CORE-33 — `wait_for_engines_ready` is sequential over 250 ports with a 2 s timeout per probe
- **P3 · world · S** · `run_genesis.py:4171-4185`
- Worst case 250 × 2 s per pass while engines boot. Fix: `asyncio.gather` the probes (preflight already parallelises its port scan, `preflight.py:236-238`).

---

## 4. Gaps vs DESIGN.md (scope: the run)

| DESIGN.md | State on `Real` |
|---|---|
| §1 "tick = 10 game-minutes; 144 ticks/game-day; day/night cycle. Agents sleep ~8 game-hours (no interactive LLM calls; learning pipeline runs then)" | Clock built (`clock.py`). Sleep = no cognition (correct), but the runner still dispatches every sleeper (CORE-17), continuing actions run overnight, and no run has reached a night with the day jobs live (§2.4). |
| §3 "Agents must be able to see all of this. Energy and money balances, current burn rate, today's upkeep and tax, who depends on them, and what any action will cost — all visible in context every tick" | Energy/money/burn/runway: built (`format_volatile_line`). Today's upkeep and tax: dead branch (CORE-16). Dependents: only the household roster, no "you pay for X". Action cost: tool only. |
| §3 "Basic tax … debited every game-day" | Wired (4688-4749) but at 0 bps by charter default, on the first tick (CORE-04), with no event (CORE-03). |
| §4 "The trace must record which lane and model actually served every call" | Broker side (out of scope) records it; world side has no `turns` table joining a perception to its calls (CORE-24). |
| §4 "Spend governor: process-wide hard USD cap computed from real response usage. Warn at 80%, stop dispatch and checkpoint at 100%" | Cap: yes (ledger burned). Warn at 80 %: not implemented in the runner (only the broker). Checkpoint at trip: only via shutdown's final checkpoint. |
| §8 Viewers "read from the trace store and the world's live state" | Agent reasoning viewer not mounted on the running host (CORE-26); location viewer has no map in embedded mode (`map_service=None`). |
| §9 "Checkpoint full world state every game-day" | Written; incomplete (CORE-08); the S3/`world_snapshots` mirror writes to memory (CORE-14). |
| §9 "event-driven scheduling (~30% of agents need a call per tick)" | Cadence 3 round-robin + interrupts; no event-driven *skipping* (an agent with nothing new is still woken). |
| §9 "The trace store is a complete causal record" / ROADMAP §8 "A run that cannot write a complete trace halts" | World `events` not written in live runs; halting on 3 consecutive failures exists but the recorder's own health is not surfaced (CORE-24). |
| §11 "~40 s/tick, ~1.1 hr per game-day" | Unpaced loop (CORE-01); measured ~63 s/tick at 20 residents; 2.5 h/game-day. |
| §12 Build path "world+tools → broker+ledger → herald seam → Supabase → game app" | The "real" world server (`main.py`) is not on the run path; the embedded harness still is (ROADMAP T3 open). |

---

## 5. Gaps vs the target

**Thriving, not surviving.** Residents cannot see what they hold, owe, promised, or are walking towards (CORE-16); the day's costs are charged invisibly at 06:10 (CORE-04, CORE-03); questions become unpayable debts in the prompt (CORE-15); nights are wasted (CORE-17). What exists: `cost_disclosure.py`, `debts_of`, `wage_promises_for`, `journey_for`, `keep_doing` — all unrendered. Add: the structured self-ledger block and a "commitments" section rendered from `continuing` + `intentions` + `wage_promises` + `loans`, in `run_genesis.dispatch_due_agents` → `retrieval_depth`.

**Organisations and government.** The charter engine runs but emits into a void in every live run (CORE-02): no election event, no amendment alert, no petition interrupt, no politics trace rows. Add the sink rewire, then an election-cycle driver (ROADMAP T3 says "every 3 game-days" belongs to "a future charter-tick module" — `run_life_ticks` is the place), and render the institutional state (who governs, tax rate, open tenders) in perception.

**Expansion and construction by the town's own builders.** In embedded mode `map_service=None` (2176, 2198, 2203), so an approved land petition "records the decision, no physical map growth" (`admin_api.py:652-663`); `build_structure` was never called (run 23). Build on: `MapService.expand_land` (only wired in `main.py`), `construction.py`. Add: give the embedded root a `MapService` when the Ville dir exists (it already builds `MovementSystem` from it), and publish `world.land.expanded` so builders perceive new plots.

**Skills/tools that cut model calls.** `keep_doing` is the only mechanism that trades model calls for world-side repetition (4335-4338); a mastered skill still costs a full turn per use. Build on: `skill_playbook`/`skill_progress` (`state.py:1279`), `advance_skill_workflows`. Add: let a resident bind an authored skill to a continuing action ("run `mill_flour_run` until stock ≥ 20") so the world walks the steps without a model call; count model-calls-per-task in the `turns` table (CORE-24) so the compounding quantity is measurable.

**Supply/demand with ideology-driven mechanics.** Price board and posted prices exist and are rendered; tax rate is a charter policy but its events do not exist on the bus and it is 0 by default. Add: CORE-02/03; a non-zero genesis rate; render "tax rate N bps set by <seat>" in perception; head-tax/upkeep as visible daily lines.

**Evolving goals.** One `intention` string per resident with day-end expiry (4679; `state.py:829-865`). Nothing rolls short-term needs → plans → ambitions. Build on: `intentions`, `memory_stream` plans, the persona's `ambitions`. Add: a world-side `goals` record (horizon, text, set_tick, progress events) written by a `set_goal`/`revise_goal` tool, rendered as three lines (today / this week / ambition), and a per-day "what became of it" line. Keep it a tool (Law 2).

**World-class instrumentation.** Add the run manifest, `turns` table, recorder health route, event schema versioning, `run_id` on every row, and a replay tool that re-renders perceptions from the `turns` table (CORE-24); fix `events` recording (CORE-03) so the report's sections read real rows; start the observatory from `runseries.sh`.

---

## 6. Recommended work items (P0 first)

1. **CORE-02 rewire the charter sink in the embedded root** (S). Spec: in `GenesisRunner.boot` after `build_world`, re-point all seven charter sub-engines at the bus. Acceptance: mock run, `schedule_election(0)`, tick to the vote phase, assert `politics.election.*` in `event_bus.recent()` and `metrics.snapshot()["elections"] > 0`.
2. **CORE-03 + CORE-04 record world events and fix the report's sources; charge at real day boundaries** (M). Spec: allowlisted `record_event` in `persist`; `charge_the_day_tax` with a bus sink; `last_life_day_processed` initialised to the boot day; `run_report` reads `economy.price.posted` and falls back to live state. Acceptance: mock run posting one price and crossing tick 144 → report shows the price, one upkeep charge per household at tick 144 and none at tick 1/37; `events` table has `economy.upkeep.charged` rows.
3. **CORE-01 + CORE-07 + CORE-12 make pacing real** (S). Spec: sleep the remainder of the interval per tick; call `apply_tick_pacing` on both boot paths; derive the interval from measured calls per turn. Acceptance: a mock run with `tick_interval_s=0.2` and 10 ticks takes ≥ 2 s; resume path logs the pacing line.
4. **CORE-05 wire the pre-halt drain into `run_continuous` and make the reserve proportional** (S). Acceptance: mock gateway records `drain_learning` exactly once before `governor.tripped` when spend crosses `cap − reserve`.
5. **CORE-17 night model** (M). Spec: no per-sleeper dispatch except the sleep-onset edge; suspend continuing actions at 22:00; keep overnight interrupts; burst night ticks. Acceptance: 48 night ticks complete in < 5 s wall with zero `/tick` calls beyond N edges; an interrupt raised at tick 140 is still due at tick 37 next day.
6. **CORE-11 asynchronous turns** (M, world + seam). Acceptance: a gateway sleeping 2× timeout receives no second `/tick` for that agent; late tool calls are attributed to the tick that issued the perception (`turns` row).
7. **CORE-09 event buffer + one `agent.moved` per resident per tick** (S/M). Acceptance: 20 walkers × 40 tiles × 3 ticks and a directed remark still delivered.
8. **CORE-08 checkpoint completeness** (M). Acceptance: round-trip test for hunger, tax debts, conversations, day-jobs ledger.
9. **CORE-13 recorder survives a re-used trace store** (S). Acceptance: seed twice, record an utterance, `stats()["rows_dropped"] == 0`.
10. **CORE-16 + CORE-15 perception: self-ledger, commitments, journey ETA, single open-questions mechanism, roster out of the volatile layer** (M). Acceptance: rendered text contains holdings/debts/ETA from a seeded world; question dropped when the asker leaves; per-tick text size does not grow with N.
11. **CORE-24 run manifest, `turns` table, recorder route, schema version** (M). Acceptance: after a mock run, `run_manifest.json` exists with genesis sha and git sha; `turns` rows == number of dispatched turns; `/world/recorder` returns `stats()`.
12. **CORE-10 stop dispatching the dead/dormant; CORE-18 health-stamp the watchdog; CORE-06 use the real dials; CORE-19 route collision; CORE-26 mount the viewer; CORE-14 mirror to the real db; CORE-20/27 remove dead backends/knobs; CORE-23 behavioural tests** (S each).
13. **T3 proper**: make `main.py` host engines (serve `/agents/{id}/tool_calls`, `/agents/{id}/memories`, `/tools/registry?agent_id`, `/tools/registry/version`) or, more cheaply, move `GenesisRunner` onto `build_world_server` (map, crime, trace `bind_event_bus`, charter sink) and demote `build_embedded_world_app` to a fixture (L).

**The concrete pacing model recommended for an affordable 24-hour game day** (Q2): (a) decouple ticks from turns (item 6) so the clock advances every `interval` seconds regardless of who is mid-turn; (b) interval derived from real calls/turn against lane capacity (item 3): at 20 residents and 200 RPM that is ~3 s/tick; at 250 residents and 600 RPM ~40 s/tick; (c) wake only on events: keep cadence as a *ceiling* (e.g. once per game-hour, cadence 6) and otherwise wake on interrupts, arrivals, completions and needs (hunger/upkeep due), so ≤ 10 turns/resident/day instead of 32; (d) `keep_doing` as the default shape of work, walked world-side, with the learning-authored skill bound to it (item "skills that cut calls"); (e) burst the night (item 5); (f) a per-resident daily call budget (derived from the wallet exactly as `budget_for` does per tick) instead of the 120 s kill. At ≈ 10 turns × 4 calls × $0.002 ≈ $0.08/resident-day, a 20-resident game day is ≈ $2 and a 250-resident day ≈ $20 — within the roadmap's rung-3 number — and, with the night bursted and ticks decoupled, ≈ 96 × 3 s ≈ 5 min wall clock per game day at 20 residents.

---

## 7. Open questions for the operator

1. Is charging upkeep (90 sim/member) on the first tick of every run intended, or has every series run been silently taxed for a day nobody lived (CORE-04)? A one-line check on the run-23 db settles it: `select count(*) from transactions where reason='daily_upkeep' and tick=37`.
2. Should the "real" server (`main.py`) become the engine host (T3 as written) or should the embedded root absorb `build_world_server`'s wiring (map, crime, charter sink, `bind_event_bus`)? The second is a day's work; the first also needs the tick loop and supervisor moved.
3. Head tax is 0 bps at genesis (`charter/models.py:260`). Is a non-zero genesis rate acceptable, or must the first tax be voted (then nothing is ever taxed until an election event exists — CORE-02)?
4. Is a 400-turn/day cadence-3 town the intended baseline, or may the runner move to event-driven wakeups with a per-day turn budget (this changes what "a tick" means for every report)?
5. For 250 residents: is the target a single world process, or may tool dispatch be sharded (per-location workers) — the event bus, ring buffer and JSON-schema regeneration are all single-loop today.
6. `smallville/` is absent from this checkout; 66 tests and the whole map path are unverifiable here. Is the map required for the next phase's runs (movement, construction) — if so, the checkout/CI must carry it.
7. Should the observatory (:8500) be part of `runseries.sh`, and should the recorder's drop counter block a run (preflight critical) rather than only stop it after three failures?
