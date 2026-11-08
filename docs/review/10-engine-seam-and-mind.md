*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line`. Finding ids (`ENG-nn`) are referenced from the cross-cutting documents.*

# Review A — the resident's mind (engine seam + frozen engine)

Prefix: **ENG**. Tree: `Real` @ `5400e94` (2026-09-03). Read-only review; nothing under
`/home/user/kasukabe` was modified. All paths are repo-relative; line numbers are
from this tree.

Scope: `engine/src/sim/*` (the seam), and the frozen parts it depends on
(`engine_core`, `memory`, `learning_centre`, `herald`), plus the world-side call
sites that drive the seam (`app/world/run_genesis.py`, `call_budget.py`,
`retrieval_depth.py`, `broker/translate.py`) where a finding cannot be explained
without them.

---

## 0. Executive summary (read this if nothing else)

1. **The learning centre cannot write a single row in the sim topology.** `pinned_boot.py:541`
   builds `MemorySystem(working_memory=working_memory)` with no episodic, knowledge, social
   or soul store, and `MemorySystem` raises `RuntimeError` on every write to a `None` store
   (`memory_system.py:29-30,126-127,155-166`). Every LC batch therefore ends with
   `wrote_something=False` (`pipeline.py:410-425`), un-marks its signals, and **never
   deletes them**. Each batch re-sends the whole history since genesis: that is the
   55–108K-token `learning_centre.social_graph` / `knowledge_extraction` calls in run 23
   (`docs/runs/run-23-report.md:1140-1232`). Soul evolution, social graph, episodes,
   knowledge, C1/C4/C5: all structurally zero, regardless of run length. **ENG-01, P0.**
2. **Every model call is emergency-compacted.** `RESERVE_FLOOR = 25_000`
   (`context_engineering.py:31`) is added to the preflight total and compared against a
   12,500-token window (`model_profile.py:166`), so `preflight_total >= context_window`
   is always true (`compaction_orchestrator.py:1061-1080`). `emergency_compact` guts all
   tool results and keeps only the first message group + the last 5 messages, then
   `finalize_compaction` appends a boundary marker, `[Context restored after compaction]
   Event: {"event": "thread_state_change"...}` and `[Post-compact tool reminder]` — visible
   as the *input* of most run-23 turns (`run-23-report.md:748,796,843,892,945-950,984,999,
   1003,1012,1051,1053,901`). The resident's day-0 06:20 perception is pinned into every
   prompt for the life of the run. **ENG-02, P0.**
3. **Tool schemas are ~13K real tokens per call and the location-reduced schemas may
   never reach the wire**: herald caches the wire tool array keyed on the *name set only*
   (`provider.py:520-531`), so the seam's per-tick in-place re-description
   (`world_tools.py:303-311`) is silently ignored after the first call. **ENG-03/ENG-16, P1.**
4. **Learning signals grow quadratically**: `Coordinator.flush_signals` flushes the entire
   working-memory log of the thread after every turn (`coordinator.py:773-778,1193-1198`),
   WM is never cleared, and (per 1) signals are never deleted. **ENG-04, P1.**
5. Assistant scaffolding still on the wire, itemised in **ENG-07**: `request_plan` complexity
   nudge (names a pruned tool), the struggle injection `[System: Execution was struggling
   (repeated tool call: world:say...)]` which trips after three `say` calls with the same
   argument keys, the MAX_TOKENS continuation prompt, compaction boundary/reinjection, the
   WM "Files Touched" session summary, and `COMPACT_PROMPT`'s developer genre.
6. **Kimi's native tool-call markup** (`world_say:0 <|tool_call_argument_begin|> {...}
   <|tool_call_end|>`) arrives as text and nothing parses it; it is fixable in the broker
   (wire-repair layer, like `legalise_transcript`) or in a seam adapter wrapper. **ENG-08.**
7. There is **no goal/need/belief model** anywhere in the resident's mind: `set_intention`
   is one ≤200-char string that expires at day end; memory-stream `plan` records are
   regex-extracted monologue nobody re-reads; `planner.py` is unreachable. A design is in
   §5.3. **ENG-24.**
8. Engine sim tests: **99 passed, 1 failed** on this tree
   (`tests/test_sim_agent_adapter.py::test_the_learn_route_consolidates_on_demand`, stale
   after the `learning_ran` semantics change). Full suite: 751 passed before the first
   bubblewrap-sandbox failure with `-x`; the full non-`-x` run did not finish within the
   review's 10-minute cap (see §8).

---

## 1. Inventory

Status legend: **LIVE** = on the production run path (`world.run_genesis` → engine process
`src.sim.sim_main` → `pinned_boot` → per-tick `agent_adapter`); **LIVE-partial** = on the
path but a documented part of it never executes in this topology; **ORPHANED-in-sim** =
imported/constructed but nothing on the sim path can reach it; **TEST-ONLY**.

### 1.1 The seam — `engine/src/sim/*` (3,262 lines)

| Path | Lines | Purpose | Status / proof |
|---|---|---|---|
| `sim/__init__.py` | 40 | Package docstring (module map). | LIVE (import). |
| `sim/sim_main.py` | 88 | Process entry: env → `SimProfile` → `boot_sim_engine` → uvicorn serving `agent_adapter` app. | LIVE. Launched by `run_genesis.py:967-972` (`python -m src.sim.sim_main`) for the default `process` backend (`run_genesis.py:536`). |
| `sim/config.py` | 400 | `SimProfile`, env map to `Settings`, fork deny rules, `SIM_MAX_RESPONSE_TOKENS=1536` (:119), awake-learning interval 18 ticks (:142), ablation arm (:145-165). | LIVE (`pinned_boot.py:358`). |
| `sim/model_profile.py` | 269 | One-model allowlist, `SIM_CONTEXT_WINDOW=12500` (:166), catalog overlay, `guarded_create_llm`. | LIVE (`pinned_boot.py:366,382-383`). |
| `sim/pinned_boot.py` | 608 | The composition root: SQLite, WM, adapters (wrapped by `resident_voice`), world tool registry, session log, memory stream, `search_memory`, persona `ConsciousnessManager`, `Coordinator`, pinned thread, tool pruning, permissions, `MemorySystem(working_memory=…)`, `LCPipeline`, `SimLearningGate`, correction store. `SimEngineState.send_tick` (:175-270). | LIVE (`sim_main.py:70`; `namespace_supervisor.py:164-172`). |
| `sim/agent_adapter.py` | 182 | FastAPI `/tick`, `/learn`, `/health`; sleep gating; `call_budget` → `send_tick`. | LIVE on the `process` backend (`HttpAgentGateway`, `run_genesis.py:1331-1380`). **Bypassed** on the `namespace` backend (`namespace_supervisor.py:254-264` calls `engine.send_tick(perception)` directly: no sleep gate, no learning gate, no call budget — ENG-11). |
| `sim/world_tools.py` | 312 | `WorldClient` (GET registry, GET version, POST tool_calls, POST memories), `build_tool_def`, per-tick `refresh_world_tool_registry` (force when local schemas). Records corrections on 4xx (:206-214). | LIVE (`pinned_boot.py:401-402,194-205,523`). |
| `sim/world_corrections.py` | 115 | Refusal → `LearningSignal(type="correction", source="engine", subject=tool)`. | LIVE (`world_tools.py:206`; store set at `pinned_boot.py:575-577`). |
| `sim/learning_gate.py` | 153 | Sleep-onset + waking-interval gate around `LCPipeline.run_batch`, `no_learning` arm. | LIVE on HTTP path (`agent_adapter.py:122,141,167`); unreachable on namespace backend. |
| `sim/resident_voice.py` | 230 | Adapter proxy: rewrites the frozen post-turn summary prompt; skips the model call for idle turns. Wraps `generate` only. | LIVE (`pinned_boot.py:397`). |
| `sim/tool_surface.py` | 100 | Deletes 7 assistant tools from the registry after thread creation. | LIVE (`pinned_boot.py:522`). |
| `sim/session_log.py` | 237 | Per-turn JSONL (input/reasoning/output/actions/done_status) from emitter events; `recall_session_log` tool. | LIVE (`pinned_boot.py:413-416`). Run 23: `recall_session_log` mentioned once. |
| `sim/memory_stream.py` | 383 | observation/plan/reflection records from emitter events → JSONL + POST to world; `recall_memories` tool. | LIVE (`pinned_boot.py:427-439`). Plan/reflection extraction depends on `ThinkingDelta` events (see ENG-14). |
| `sim/memory_query.py` | 145 | `search_memory` tool over `Search.unified()` (signals leg only). | LIVE (`pinned_boot.py:457-461`). Run 23: 3 mentions. |

### 1.2 Frozen engine parts the seam depends on

| Path | Lines | Role in the sim | Status |
|---|---|---|---|
| `engine_core/thread.py` | 4,972 | `ExecutionThread`: `initiate` (:2212-2340), `execute` (:2645), `execute_turn` (:2777), `agent_loop` (:3202-4582), `run_single_tool` (:4584), post-turn signal capture (:4233-4283). | LIVE. |
| `engine_core/coordinator.py` | 1,292 | `handle_input` (:281) → `build_dispatch` explicit-thread bypass (:457-475) → `execute_segment` (600 s task timeout :742) → `flush_signals` (:773-778,1187-1215). Router never set (:271). | LIVE. |
| `engine_core/context_engineering.py` | 1,946 | Token estimates (:343-365), `needs_compaction` (:1016-1021, 0.80), `compact_messages`, `emergency_compact` (:1159-1234), pairing repair (:1237-1370), `COMPACT_PROMPT` (:86-140), summariser (:1702-1946). | LIVE (emergency path every call; LLM path cannot succeed at 12.5K — ENG-02). |
| `engine_core/compaction_orchestrator.py` | 1,945 | `compact_if_needed` (:705-1136), session-memory compact (:1500-1574), `finalize_compaction` (:1576-1660), reinjection (:1869-1916), tool re-announcement (:1809-1824). | LIVE. |
| `engine_core/context_collapse.py` + `importance_scoring.py` | 737 + 203 | Collapse pre-pass, enabled by default (`thread.py:904-907`, `FEATURE_CONTEXT_COLLAPSE`). | LIVE-partial (runs; emergency step dominates). |
| `engine_core/token_budget.py` | 171 | Per-turn *output* budget loop. | ORPHANED-in-sim: `token_budget_enabled` default False (`config.py:374`), never set by `sim/config.py`. |
| `engine_core/planner.py` | 360 | Charter creation for `request_plan`. | ORPHANED-in-sim: `request_plan` is pruned (`tool_surface.py:45-66`); struggle auto-plan finds no tool (`thread.py:3603-3623`). |
| `engine_core/dispatch_parser.py` | 187 | Only `single_segment`/`normalize_whitespace` reach the sim (`coordinator.py:26,474-487`). | LIVE-partial. |
| `engine_core/agent_summarizer.py` | 204 | Background progress summariser. | ORPHANED-in-sim: no references from `thread.py`, `coordinator.py` or `sim/`. |
| `engine_core/core_soul.json` | 73 | "September" identity. | LOADED (`consciousness.py:56-60`) and built into `thread.consciousness` (`coordinator.py:1157-1158`) but **not rendered**, because `register_prompt_sections` is skipped when the seam pre-registered a section (`thread.py:2796`). Fragile — ENG-07(j). |
| `memory/consciousness.py` | 411 | Section registry + `build_prompt_blocks` (:315-350). | LIVE (one STABLE section: persona). DYNAMIC layer (soul/social) never built: all stores `None` (`pinned_boot.py:485`). |
| `memory/soul.py` | 143 | Evolved soul store. | ORPHANED-in-sim (no `Soul` constructed anywhere under `sim/`). |
| `memory/social_graph.py`, `episodic_memory.py`, `knowledge_store.py`, `embeddings.py`, `portability.py` | 511/265/386/398/386 | Durable stores. | ORPHANED-in-sim (never constructed; `MemorySystem` writes raise — ENG-01). |
| `memory/memory_system.py` | 356 | Facade. | LIVE with WM only. |
| `memory/sqlite_working_memory.py`, `wm_formatting.py`, `working_memory.py` | 191/213/34 | WM log; deterministic "Current State / Task Activity / Tool Operations / Files Touched" summary (`wm_formatting.py:115-118`). | LIVE (session-memory compaction path). |
| `learning_centre/pipeline.py` | 922 | `run_batch` (:243-437), clustering (:483-520), consolidate (:690-800), step 8/11 (:819-922). | LIVE — runs, writes nothing (ENG-01). Contains "AUTHORISED LAW 1 EXCEPTION" edits (:180,:282). |
| `learning_centre/signals.py` | 221 | `SignalStore` write/pull/mark/unmark/delete/FTS/`flush_wm` (:157-221). | LIVE. |
| `learning_centre/scheduler.py` | 235 | 6-hour wall-clock scheduler. | ORPHANED-in-sim (replaced by `learning_gate.py`; never constructed). |
| `herald/provider.py` | 894 | Anthropic-shaped request build (:440-518), tool-schema cache (:520-531), SSE parse (:599-729), wire mapping (:731-894). | LIVE (via broker at `CDT_URL`). |
| `herald/transport.py`, `catalog.py`, `catalog_sync.py`, `defaults.py`, `retry.py`, `usage_tracker.py` | — | Transport, overlay catalog (context window lookup `catalog.py:66-71`), retry. | LIVE. |
| `herald/model_validation.py`, `token_budget_parser.py`, `quota_cooldown.py` | — | | ORPHANED-in-sim (validation deliberately skipped, `pinned_boot.py:5-18`; budget parsing off). |

### 1.3 Tests touching the seam

Engine: `tests/test_sim_*.py` (9 files), `test_resident_voice.py`, `test_memory_on_demand.py`,
`test_the_world_corrects.py`. App: `world/tests/test_residents_get_the_towns_tools.py`,
`world/tests/test_character_gates.py`. None of them boots the real `LCPipeline` against the
sim's `MemorySystem` and asserts a durable row — which is why ENG-01 was invisible.

---

## 2. How it works today — one tick, end to end

### 2.1 World side (drives the seam)

1. `TickEngine.tick` → `GenesisRunner.dispatch_due_agents` (`run_genesis.py:4322`). A resident
   under `keep_doing` is advanced by the world and not woken (`:4335-4338`).
2. `is_sleeping = not timestamp.is_daytime` (`:4342`; `clock.py:63`). Night = asleep, no
   per-resident schedule.
3. Perception rendered by `render_perception_text` (`retrieval_depth.py:926-1150`) in this order:
   ambient → why you stopped working → intention line (`format_intention`, :433-465) →
   what was said to you → open questions → what you asked and got no answer → standing
   brief → **the energy line** (wallet, last-turn cost, runway) → silent-turns fact →
   conversation/convening hint → who is here → price board → heard events →
   relationship notes → skill playbook → authoring hint → gap notice → the closing
   line "Nothing you have thought or written above has happened yet…". This is the
   only VOLATILE layer the resident gets (see 2.3).
4. `gateway.send_tick(agent_id, tick, is_sleeping, perception, call_budget=…)` under
   `asyncio.wait_for(..., tick_timeout_s=120)` (`:4447-4470`; `:574`). `call_budget_for`
   (`:3555-3569`) → `call_budget.budget_for` (`call_budget.py:103-135`): 5% of the wallet
   ÷ 0.75¢ per call, clamped to **[2, 12]**. At the $5.25-5.85 wallets of run 23 that is
   35-39 affordable → capped at **12 calls per tick**.

### 2.2 Engine HTTP receiver

5. `POST /tick` (`agent_adapter.py:116-151`). Asleep → `learning_gate.on_tick(True)` only.
   Awake → `learning_gate.on_tick(False)` **first** (`:141`; runs a batch every 18 waking
   ticks, `learning_gate.py:100-107`, `config.py:142`, on the same Kimi adapter and inside
   the same 120 s), then `engine.send_tick(perception, call_budget)`.

### 2.3 `SimEngineState.send_tick` and prompt assembly

6. `pinned_boot.py:175-270`: refresh world registry (every tick, `force=True` when
   `KASUKABE_LOCAL_TOOL_SCHEMAS` — :194-205) → per-engine turn lock → `session_log.start_turn`,
   `memory_stream.start_turn` → `RequestOptions(max_turns=call_budget)` (:253-257) →
   `coordinator.handle_input(text, thread_id="kasukabe-agent-<id>")`.
7. `Coordinator.handle_input` (`coordinator.py:281-455`): `self.search is None` (never passed
   by `pinned_boot`) so **no memory prefetch/recall ever runs** (:327, :1043).
   `build_dispatch` explicit thread → `single_segment` (:457-475). `execute_segment` runs
   `thread.execute` under a 600 s task timeout (:742) and afterwards spawns
   `flush_signals` (:773-778).
8. `ExecutionThread.execute` (`thread.py:2645-2732`): turn lock (30 s), `UsageAccumulator`,
   WM `SYSTEM_EVENT {"user_input": …}`. `execute_turn` (:2777-):
   - `consciousness_mgr.get()` builds a `ConsciousnessContext` whose `core_soul` is the
     packaged "September" document (`consciousness.py:56-60`), but sections were
     pre-registered by the seam so `register_prompt_sections` is skipped (:2796).
   - `build_prompt_blocks()` (:2809) → **one STABLE block: `persona.txt`** (median 5,181
     chars ≈ 1.3K tokens measured on `genesis_20.json`; persona + law of the town), cache
     scope global/ttl 1h → `cache_control` on the last system block (`provider.py:479-486`).
     There is **no DYNAMIC block** (no soul/social stores) and **no engine VOLATILE block**;
     the world's perception is the volatile layer, delivered as the user message.
   - `<system-reminder>` assembly (:2840-2930): no recall (search None), no skills
     (`skill_catalog` None), **complexity nudge** if `complexity_score(perception) >= 5`
     (:508-520, :2919-2923) — a perception >500 chars with "then"/"next"/"first"/"step"
     phrases scores ≥5 — prepends "This task appears complex. Consider calling
     `request_plan`…" (tool pruned).
   - History: `conversation_store.load_messages` (:2952-2975). What is stored per tick is
     **only** `(perception, full_text)` (:3083-3108) — no tool_use/tool_result blocks survive
     between ticks.
   - `context_window = get_context_window(model)` = **12,500** (`catalog.py:66-71` ←
     `model_profile.py:166`).
9. `agent_loop` (:3202-4582):
   - `tools_schema = self.tools.to_tool_defs()` — **every** ToolDef in the registry
     (`tool_runtime.py:137-155`): 76 world tools + `recall_session_log`, `recall_memories`,
     `search_memory` (`create_skill` is not registered: `data_dir` is empty, `thread.py:1823`;
     the other 7 engine tools were pruned). `max_turns = call_budget` (:3307).
     `escalation_eligible` is False because `settings.max_tokens` (1536) ≠ default 4096
     (:3392-3396), so MAX_TOKENS recovery is continuation-only.
   - Per iteration: WM-restored struggle counters (:3399-3406) → struggle check (:3584-3641)
     → `compact_if_needed` (:3645) → freeze prefix → `adapter.stream_with_tools(messages,
     system, tools, max_output_tokens=None)` (:3817-3822) → the broker (`CDT_URL`) →
     Bedrock Converse. `max_tokens` on the wire = `Settings.max_tokens` = **1536**
     (`provider.py:467`).
   - Tool calls: `StreamingToolExecutor` → `run_single_tool` (:4584-4760): `ToolExecution`
     events (session log / memory stream), `check_permission` (:4709) — `world:*` ALLOW rule
     (`world_tools.py:78-98`), INTERNAL tools auto-allowed (`permissions.py:460-464`) →
     `execute_tool` → `WorldClient.call_tool` POST (`world_tools.py:181-221`); 4xx → text
     "Error: …" back to the model **and** a `correction` signal (`world_corrections.py:74-82`).
   - End of turn without tool calls: MAX_TOKENS → up to 3 continuations, each a billed loop
     turn with the prompt "Output token limit hit. Resume directly — no apology…"
     (:4106-4159); post-turn signal capture (:4233-4283) via `adapter.generate` → the
     `_ResidentVoiceAdapter` rewrites the prompt or, if the turn made no tool call, returns
     a deterministic string with no model call (`resident_voice.py:194-201`) → written as
     `LearningSignal(type="event", source="user", subject=perception[:40])`.
   - Exits: end_turn, `MAX_TURNS` when the call budget is spent (:4563-4566), REFUSAL,
     MAX_TOKENS exhausted, budget.
10. `execute_turn` tail (:3083-3160): persist `(user_input, full_text)`; `ExecutionSummary`
    (status, exit_reason, num_turns = model calls, tool_calls_count) → `SessionLogWriter._flush`
    (`session_log.py:146-167`) and `MemoryStreamWriter._close_turn` (`memory_stream.py:185-235`)
    → POST `/agents/{id}/memories` (`world_tools.py:223-241`).
11. `TickResponse{reply, learning_ran, steps_used, budget_hit}` (`agent_adapter.py:143-151`).
    The world records `budget_hit` → next perception carries `spent_line`
    (`call_budget.py:138-146`). The prose `reply` is not consumed by the world.

### 2.4 Where each limit lives

| Limit | Value | Where |
|---|---|---|
| Model calls per tick | 2..12 from wallet | world `call_budget.py:46-47,103-135` → `RequestOptions.max_turns` (`pinned_boot.py:253-257`) → `thread.py:3307,3484` |
| Engine default max turns | 200 | `thread.py:314` (never reached) |
| Output tokens per call | 1536 | `sim/config.py:119,223` → `Settings.max_tokens` → `provider.py:467` |
| MAX_TOKENS continuations | 3 | `thread.py:3348` |
| Dispatch wall-clock | 120 s | `run_genesis.py:574,4457`; HTTP client timeout equal (`:3854`) |
| Engine task timeout | 600 s | `coordinator.py:742` |
| Turn lock acquire | 30 s | `thread.py:2661` |
| Context window (client-side) | 12,500 | `model_profile.py:166` |
| Compaction threshold | 0.80 × window = 10,000 | `context_engineering.py:32,1016-1021` |
| Reserve floor | 25,000 | `context_engineering.py:31` (see ENG-02) |
| Learning batch cadence | sleep onset; every 18 waking ticks; `/learn` at shutdown | `learning_gate.py:97-113`, `config.py:142`, `run_genesis.py:1383-1409,5264,5288` |
| Post-turn summary | every turn with ≥20-char input; idle turns free | `thread.py:4235`, `resident_voice.py:109-111` |

---

## 3. Answers to the seven questions

**Q1 (the tick):** §2 above.

**Q2 (assistant-shaped scaffolding still on the wire):** ENG-07 enumerates ten items with
line numbers. `resident_voice.py` rewrites exactly one of them (the post-turn summary
prompt, `FROZEN_SUMMARY_PROMPT`) and short-circuits it on idle turns; it does not touch the
compaction summariser (`COMPACT_PROMPT`), the session-memory summary, the complexity nudge,
the struggle injection, the MAX_TOKENS continuation prompt, or the post-compaction
reinjection, and it wraps `generate` only — never `stream_with_tools`.

**Q3 (goals/plans/needs/beliefs):** none in the engine layer. What exists: (a)
`set_intention`/`drop_intention` (`intent.py`), one string ≤200 chars, optional
`done_when_tool`, expires at day end (`state.py:852`), rendered as one line
(`retrieval_depth.py:433-465`); run 23 shows 3 `set_intention` mentions and two refusals
because the resident passed the sanitised name `world_cook` (`run-23-report.md:3513,4654`).
(b) `memory_stream.py` `plan` records: regex over `ThinkingDelta` text (:84-88, :210-214);
re-readable only through `recall_memories` (1 mention in run 23). (c) `planner.py`/charter:
unreachable (request_plan pruned; auto-plan finds no tool). (d) `keep_doing`
(`living.py:374-399`) is a committed action with `until` conditions — the closest thing to
a plan. Design in §5.3 and ENG-24.

**Q4 (learning):** what consolidates = the post-turn `event` signal (model paraphrase, or
the free idle string), `flush_wm`'s `tool_call`/`tool_result`/`system_event`/`outcome`
signals for the whole WM every turn, and `correction` signals from refusals. When = sleep
onset (never occurred), every 18 waking ticks (fires mid-morning; run 23 rows 1140-1237),
and `/learn` at shutdown. Sizes: episode call ≈ 11-13K tokens (capped at last 120 signals,
`pipeline.py:189,307`); knowledge clusters: with no embedding service, grouped by 30-char
subject prefix (`:483-492`) — many 300-1,500-token clusters plus giant ones; social call
sends **all** signals (`:262-273`) → 55-108K tokens. Why 100K: signals are never deleted
(ENG-01) and are duplicated every turn (ENG-04). Corrections: written (70 in run 23) and
visible to the knowledge agent as `correction`-typed rows; `step11_skill_evolution` cannot
match them (`source='user'` vs the seam's `engine`, and it also needs `skill:*` tool_call
subjects that do not exist — ENG-05). Skill artifact ever produced: **no**; nothing in the
sim can produce or load one (ENG-06). Two skill systems: world proficiency+workflow
(`skills` table, `skill_artifact_text`, rendered into perception) vs engine
`user_skills`/catalog skills (unreachable). They do not relate.

**Q5:** ENG-08 (markup), ENG-09 (prose-only), ENG-10 (orphan legalisation).

**Q6:** ENG-02/03/16 and §6 items 2-3 (how to reach p95 ≲ 8K).

**Q7:** ENG-11..ENG-27.

---

## 4. Findings

Severity: P0 blocks the target / corrupts data or money; P1 major gap; P2 defect; P3 polish.
Layer: seam / world / registry / broker / engine-finding. Effort S/M/L.

### ENG-01 — The learning centre cannot write anything in the sim (P0, seam, S/M)

- `engine/src/sim/pinned_boot.py:541` — `memory_system = MemorySystem(working_memory=working_memory)`.
- `engine/src/memory/memory_system.py:29-30` ("Writes on None components raise RuntimeError"),
  `:126-127` (`create_episode`), `:155-156,166-171` (`write_knowledge_*`), and the social/soul
  writers (13 raise sites).
- `engine/src/learning_centre/pipeline.py:324-350` (episode write inside `try` → appended to
  `result.errors`), `:376-380` (knowledge write → `write_knowledge_entry` → `except: return None`),
  `:381-386` (social), `:696-697` (`consolidate` returns immediately when no episode/knowledge
  was written → **soul updates never run**), `:410-425` (`wrote_something` False →
  `unmark_processed`; `delete_processed` never called).
- Evidence: run 23 `auto:learning_centre.*` rows with 55-108K input and 6-14 output tokens
  (`run-23-report.md:1146,1155,1162,1172,1220,1232`); `docs/SYSTEM_REVIEW.md` "0 of 20";
  `agent_adapter.py:80-86` (run 22: 7,138 signals unconsolidated).
- Why it matters: every compounding quantity (soul drift, social graph, C1/C4/C5, skill
  evolution) is zero by construction, not by run length. The waking-interval batch costs
  $0.02-0.04 per resident per 18 ticks for nothing.
- Fix (seam): construct the stores the pipeline writes — `EpisodicMemory(db, None)`,
  `KnowledgeStore(db, None)`, `SocialGraph(db, None)`, `Soul(db)` — all accept
  `embedding_service=None` (`episodic_memory.py:41-47,60-73`; `knowledge_store.py:54-60,77`;
  `social_graph.py:45-51`) and skip the vec insert when no embedding; pass them to
  `MemorySystem` and to `ConsciousnessManager(soul=…, social_graph=…, episodic_memory=…,
  knowledge_store=…)` so the DYNAMIC layer (T1) finally renders; wire
  `LCPipeline(on_data_changed=consciousness_mgr.invalidate)`. Check migrations `001` create
  the tables on the engine DB (they do: `migrations/001_memory_schema.sql`). Then verify
  keyword-only search works with zero/no embeddings (`episodic_memory.py:101`). Add a boot-level
  test: fake broker returns valid JSON for the three encoders → `run_batch` → assert one
  `episodes` row and one `knowledge_store` row.
- Also add a seam guard: if `LCPipelineResult.errors` all read "not configured", refuse to
  report `learning_ran` and log loudly.

### ENG-02 — Every model call is emergency-compacted; the first perception is pinned forever (P0, seam + engine-finding, M)

- `engine/src/engine_core/context_engineering.py:31` `RESERVE_FLOOR = 25_000`;
  `compaction_orchestrator.py:1061-1080` `preflight_total = post_tokens + sys_tokens +
  tools_tokens + RESERVE_FLOOR; if preflight_total >= context_window: emergency_compact`.
  With `context_window = 12_500` (`model_profile.py:166`) this is true with **zero** messages
  (tools alone estimate ≈ 53K chars/4×1.2 ≈ 16K). It was also always true at the previous
  40,000 window (16K + 1.5K + 25K > 40K); only the original 256K window escaped it.
- `emergency_compact` (`context_engineering.py:1159-1234`): guts *all* tool results, keeps
  `groups[0]` (the resident's first stored message = day-0 06:20 perception) + trailing
  groups covering 5 messages, truncates.
- `finalize_compaction` (`compaction_orchestrator.py:1576-1660`) then appends
  `COMPACTION_BOUNDARY_CONTENT` + `<!-- compact_meta … -->`, then `build_reinjection_messages`
  (`:1869-1916`: last 3 WM checkpoints + last 5 WM `SYSTEM_EVENT`s as `Event: {json}` — these
  are `thread_state_change`, `{"user_input": …}` and raw turn text written by
  `thread.py:938-956,2700-2712,4092`), then `[Post-compact tool reminder] These tools remain
  available…` (`:1809-1824`), then `post_compact_cleanup` → `consciousness_mgr.invalidate()`
  every call.
- Evidence: run 23 turn inputs beginning `[Context restored after compaction] Event:
  {"thread_id": "kasukabe-agent-n01", "charter_v…` and `[Post-compact tool reminder]`
  (`run-23-report.md:748,796,843,892,901,945,948-950,984,999,1003,1012,1051,1053`); a
  resident diagnosing it: "Compacted across my attempted action … the compaction is
  occurring BETWEEN my thought and the action" (`:2206`); `model_profile.py:124-126`
  ("the largest body still carrying the agent's very first turn at game-day 0, 06:20").
- Consequences: (1) the model never sees its own tool results from earlier in the same
  tick's history beyond the last 5 messages; (2) assistant-genre junk is in every prompt;
  (3) a `CompactionEvent` per call and a cold prompt cache every call (`post_compact_cleanup`
  + prefix rewrite); (4) the LLM compaction path (`compact_messages_with_summary`) can never
  succeed — `summarize_old_messages` pre-check `total_tokens >= context_window` always
  (`context_engineering.py:1760-1812`) so it returns "" after 3 PTL drops — which is
  accidentally good (that prompt is `COMPACT_PROMPT`, a software-dev genre, :86-140), but
  the session-memory path can (`compaction_orchestrator.py:1500-1574`) and injects
  "This session is being continued from a previous conversation that ran out of context …
  Current State / Task Activity / Tool Operations / Files Touched" (`wm_formatting.py:115-118`).
- Fix (seam, no engine change): (a) set `DISABLE_AUTO_COMPACT=1` in `raw_process_env`
  (`compaction_orchestrator.py:760-761` honours it) so the engine never rewrites the prefix;
  (b) bound history in the seam instead: replace `coordinator.conversation_store` (attribute
  set at `coordinator.py:251`; propagated to the thread at `:1173`) **before**
  `get_or_create_thread` with a subclass whose `load_messages` returns the last *N* stored
  pairs plus a seam-written rolling summary in the resident's voice (from the session log,
  free); (c) raise `KASUKABE_CONTEXT_WINDOW` to the real value so `cap_turn_tool_results`
  (25% of window) and `guard_tool_results` stop truncating world results at 3K tokens.
- Engine finding: `RESERVE_FLOOR` is an absolute constant; any window below ~45K makes
  the preflight unconditional. Should be `min(25_000, 0.2 × window)`.

### ENG-03 — All ~79 tool schemas are sent on every call; they are the dominant input cost (P1, world/registry + seam, M)

- `thread.py:3304` `tools_schema = self.tools.to_tool_defs()`; `tool_runtime.py:137-155`
  returns every registered tool; `filter_ad_tools_by_budget` applies only to `ad:` tools.
- Size: 76 tools = 52,965 chars (`docs/STAKES_TIME_COMPOUNDING.md` §5) ≈ 13K real tokens,
  16K by the engine's estimator. Persona ≈ 1.3K. Perception ≈ 0.3-0.4K. Run 23 per-turn
  input 15-20K: the schemas are two thirds of every bill.
- The world's location-reduced schemas (`call_budget.local_tool_schemas`) save 13% of chars
  and, per ENG-18, may not reach the wire at all.
- Fix: world serves ≤ ~20 full schemas per (location, role, seat) and a one-line verb index
  for the rest (the resident can `describe_primitives` a verb before using it — already a
  tool); strip price prose from descriptions (they are 46 tools × a sentence); then ENG-18.
  Target ≤ 4K tokens of tools. Engine finding: no per-turn tool filter hook for non-`ad:`
  tools; `defer_loading` is Anthropic-only and Bedrock ignores it.

### ENG-04 — Learning signals grow quadratically (P1, seam + engine-finding, S)

- `coordinator.py:773-778` spawns `flush_signals` after **every** turn; `:1193-1198` reads
  `self.memory.read(thread_id)` — the whole WM log (24 h TTL, never cleared for a pinned
  thread) — and `SignalStore.flush_wm` re-inserts every entry (`signals.py:157-221`, no
  watermark). After N turns the table holds O(N²) rows. `delete_processed` never runs
  (ENG-01). The world's `signal_groundskeeper.py:31-61` deletes only `system_event` rows.
- Evidence: `docs/TEST_RUN.md:690-696` (118,179 replayed records, 36,673 `thread_state_change`).
- Fix (seam): subclass `SignalStore` with a per-thread flush watermark (last WM timestamp
  flushed) and a hard cap, and assign it to `coordinator.signal_store` **before**
  `get_or_create_thread` (the thread copies the reference at `coordinator.py:1174`). Pass the
  same instance to `LCPipeline(signal_store=…)` and to `Search`. Engine finding: `flush_wm`
  needs a watermark; `pipeline.py:262-273` needs a cap like `EPISODE_CONTEXT_MAX_SIGNALS`.

### ENG-05 — `step11_skill_evolution` can never fire in the sim (P1, engine-finding + seam, S)

- `pipeline.py:875-878` reads `type='correction' AND source='user'`; the seam writes
  `source="engine"` deliberately (`world_corrections.py:22-26,80`). `:885-888` also requires
  `tool_call` signals whose subject starts with `skill:` — nothing in the sim writes those
  (`flush_wm` subjects are tool names, `signals.py:184-190`; world tools are `world:*`).
  Even if it fired it only bumps `user_skills.correction_count` (`:906-915`) — no artifact.
- Fix: do not chase step 11. Put the "world as corrector" loop in the seam (see §6 item 5):
  corrections → a seam-side per-tool refusal ledger → rendered as the resident's own
  "things I have learned the hard way" (a DYNAMIC section or perception block) and consumed
  by the knowledge encoder (already possible once ENG-01 lands, since `correction` is in
  `MEANINGFUL_USER_TYPES` only for `source='user'`, `pipeline.py:196,461-464` — note the
  trivial-task filter ignores engine-sourced corrections, so a thread with only corrections
  and no event signal gets no episode).

### ENG-06 — No engine skill artifact has ever been produced or could be loaded (P1, engine-finding + seam, M)

- `skill_catalog` is `None` (`Coordinator.__init__` default, `pinned_boot.py:499-508` does not
  pass one) → `build_skill_context` / `record_matched_skills` never run (`thread.py:2905-2917`).
- `create_skill` is not registered (`thread.py:1823` `if not self.data_dir: return`).
- `catalog.load_skills_from_roots` has no sim caller. `user_skills` is written only by step 11
  (ENG-05).
- The world's skills (`skillify`, `skill_artifact_text`, proficiency) are a separate system
  and already reach the prompt via `format_skill_playbook` (`retrieval_depth.py:1101-1104`).
- Fix: declare the world's skill workflow **the** skill artifact; make it cheaper cognition
  by (a) rendering held skills as a per-resident DYNAMIC prompt section (cached, invalidated
  on `revise_skill`) rather than per-tick perception text, and (b) letting a mastered skill
  run under `keep_doing` with no model call. Engine finding: the artifact loader assumes a
  filesystem skills root per engine; the seam could write the world's workflow into
  `<SEPTEMBER_HOME>/skills/<name>/SKILL.md` and construct a `SkillCatalog` — worth a spike
  only if the catalog's matching (needs embeddings) works keyword-only.

### ENG-07 — Assistant-shaped prompt scaffolding still on the wire (P1, seam + engine-finding, M)

| # | Text on the wire | Where | Trigger in the sim |
|---|---|---|---|
| a | `<system-reminder>This task appears complex. Consider calling request_plan…` | `thread.py:2919-2923`, `complexity_score` :508-520, threshold 5 | any perception >500 chars containing "then/next/first/step" — most of them; measured 61/240 rows. `request_plan` does not exist (pruned). |
| b | `[System: Execution was struggling (repeated tool call: world:say:['content','listener_agent_id']). A plan has been triggered. Follow the updated goals.]` | `thread.py:3584-3634`; `loop_state.py:20-23` (`STRUGGLE_REPEATED_CALLS=3`, sigs kept across ticks via WM :3399-3406,:4436-4443) | three `say` calls with the same argument keys within the last 12 calls — i.e. any conversation. Also fires on 3 consecutive refusals and 8 turns without text. No plan is created (tool pruned). |
| c | `Output token limit hit. Resume directly — no apology, no recap…` | `thread.py:4134-4145` | 18 turns in run 23; each continuation is a billed call that consumes the tick's call budget. |
| d | `<compaction boundary> <!-- compact_meta … -->`, `[Context restored after compaction] Event: {"event":"thread_state_change"…}`, `[Post-compact tool reminder] These tools remain available…` | `compaction_orchestrator.py:1615-1650,1869-1916,1809-1824` | every call (ENG-02). |
| e | `This session is being continued from a previous conversation that ran out of context… Current State / Task Activity / Tool Operations / Files Touched` | `compaction_orchestrator.py:1543-1551`; `wm_formatting.py:115-118` | when session-memory compaction fits under 10K. |
| f | `COMPACT_PROMPT` ("Files and Code Sections", "Errors and Fixes"…) | `context_engineering.py:86-140` | cannot currently succeed (ENG-02) but will the moment the window is raised; `resident_voice.py` does not rewrite it. |
| g | Post-turn summary signal: rewritten by `resident_voice.py:87-106` — OK; but `subject=user_input[:40]` and `source="user"` | `thread.py:4273-4280` | every turn. |
| h | `search_memory` description: "Recall already runs automatically at the start of a turn" | `memory_query.py:118-125` | false in the sim (no `search` on the coordinator). |
| i | `[Thread terminated by external command]`, `[Budget limit … reached. Session cost …]`, `[Tool execution interrupted — output token limit reached]` | `thread.py:3534,4088,4110-4113` | cancel / budget paths. |
| j | Latent: the packaged "September" core soul is loaded (`consciousness.py:56-60`) and built into `thread.consciousness` (`coordinator.py:1157-1158`); it is not rendered only because `register_prompt_sections` is guarded by `if not sections` (`thread.py:2796`). | | one future `invalidate_all()` + re-registration away from "Your name is September" on the wire. |

- Fix (seam): extend `_ResidentVoiceAdapter` to wrap `stream_with_tools` and rewrite the
  *messages* on the way out: drop (a) and (b) entirely, replace (c) with a resident-voice
  sentence, strip (d)/(e) (or, per ENG-02, stop them at source with `DISABLE_AUTO_COMPACT`),
  rewrite (f) if the window is raised. Add a wire-level assertion in the fake-broker test
  that no request body contains `request_plan`, `Execution was struggling`, `September`,
  `[user]`, `Files Touched`. Engine findings: struggle detection has no off switch; the
  complexity nudge has no flag; reinjection dumps raw WM JSON.

### ENG-08 — Kimi's native tool-call markup arrives as text and nothing parses it (P1, broker preferred / seam, S)

- Evidence: `run-23-report.md:4427,4437,4497` — `world_say:0 <|tool_call_argument_begin|>
  {"content": "…", "listener_agent_id": "n07", "conversation_id": "…"} <|tool_call_end|>
  <|tool_calls_section_end|>`; `world_move:9 <|tool_call_argument_begin|> {"destination_location_id": "business:b_mill"}`.
  Note the model uses the **sanitised** name (`world_say`, `provider.py:872-894`) with an
  index suffix.
- Path: Bedrock returns it as a text block; the broker translates only `toolUse` blocks
  (`translate.py:672-690`); herald yields `tool_call` only from `content_block_start
  type=tool_use` (`provider.py:653-682`); `thread.py:3823-3830` appends text.
- Fix, broker (recommended — it is already the wire-repair layer, cf. `legalise_transcript`
  `translate.py:185-240`): in `anthropic_sse_events_from_converse_stream`, buffer text and
  when a `<|tool_call_argument_begin|>` … `<|tool_call_end|>` pair closes, emit a synthetic
  `content_block_start{tool_use, id="toolu_kimi_<n>", name=<prefix before ':'>}` +
  `input_json_delta` + `content_block_stop`, set `stop_reason=tool_use`, and drop the
  markup text. The trace then records a real tool call. Fix, seam alternative: the same
  parse inside a `stream_with_tools` wrapper in `resident_voice.py`, reverse-mapping via
  `provider.tool_name_reverse_map`. Registry: accept `world_`/`world:` prefixes wherever a
  resident names a tool (`intent.py:57-70` refused `world_cook`).

### ENG-09 — Prose-only turns are structurally reinforced (P1, seam, M)

- 152/349 sessions were one prose turn (brief). Causes in this layer: (1) the stored history
  is `(perception, full_text)` only (`thread.py:3083-3108`) — every earlier "self" in the
  context is a paragraph of narration with no `tool_use` block, so the genre the model sees
  itself writing is monologue; (2) ENG-02 guts the few tool results that do exist; (3) the
  post-turn summary and reinjection are more prose; (4) 1536 cap + continuation prompt
  rewards resuming the monologue.
- Fix (seam): the `ConversationStore` view from ENG-02 stores, as the assistant turn, a
  compact **action record** built from the session log ("I said to Marta: '…'. Heard by X,Y.
  I bought 2 bread for 60 sim.") and, when the turn was silent, exactly that fact — so the
  model's own history reads as acts, not essays. Keep `full_text` in the session log for
  research. Consider `max_tokens` 700 with the continuation prompt suppressed (a 1536-token
  turn with no tool call is never useful; a tool call needs <200).

### ENG-10 — Orphan tool_result legalisation exists twice with different semantics (P3, broker/engine-finding, S)

- Engine: `repair_tool_use_result_pairing` (`context_engineering.py:1237-1370`) **strips**
  orphaned results and inserts `[Orphaned tool result removed due to conversation resume]`;
  runs only inside compaction paths.
- Broker: `legalise_transcript` (`translate.py:185-240`) **rewrites** orphans to text
  `[result of an earlier action] …` and merges consecutive same-role turns.
- In the sim, orphans arise within a tick (MAX_TOKENS tombstones `thread.py:4109-4113`,
  cancellations `:3531`) and after emergency compaction. Keep the broker repair (it
  preserves information) but log a counter per agent so a rising rate flags an engine
  invariant break rather than hiding it; the engine finding is that `emergency_compact`
  can split a round when `groups[0]` is an assistant with tool_use whose results were
  gutted.

### ENG-11 — The namespace backend bypasses the adapter contract entirely (P1, world, S)

- `run_genesis.py:748-761` `NamespaceAgentGateway` defines `tick(agent_id, perception)`;
  `dispatch_due_agents` calls `self.gateway.send_tick(agent_id, tick=…, is_sleeping=…,
  perception=…, call_budget=…)` (`:4447-4456`) → `AttributeError` on every due agent.
  `NamespaceSupervisor.send_tick` (`namespace_supervisor.py:254-264`) calls
  `engine.send_tick(perception)` with no `call_budget` and no learning gate / sleep gate.
- Default backend is `process` (`:536`), so run 23 was unaffected; `docs/SELF_AUTHORING_AGENTS.md`
  says namespace is the default — stale. The 50-250-resident path (the only one that fits
  in memory) is broken at the seam boundary.
- Fix: give `NamespaceAgentGateway.send_tick` the same signature and route through
  `agent_adapter`'s logic (extract the `/tick` body into a plain function in
  `agent_adapter.py` callable in-process).

### ENG-12 — The 120 s dispatch limit abandons turns that keep running and billing (P1, world + seam, S)

- `run_genesis.py:4457` `asyncio.wait_for(gateway.send_tick(...), timeout=120)`; HTTP client
  timeout equal (`:3841-3856`). On timeout the runner marks the agent decided and drops the
  reply; the engine turn runs to completion (up to 12 calls + 3 continuations + summary),
  bills, and the next tick blocks on `_turn_lock` (`pinned_boot.py:235`) → cascading
  timeouts (10 discarded turns in run 23).
- Fix: world derives `tick_timeout_s` from the call budget (≈ 10-15 s × calls); seam: on
  a deadline, set `thread.cancel_requested = True` so the loop exits at the next iteration
  (`thread.py:3530-3535`) and the world gets a partial result instead of nothing.

### ENG-13 — `learning_ran` means two things, and the sim suite is red on it (P2, seam, S)

- `/tick` reports `awake_batch is not None` (`agent_adapter.py:148`); `/learn` reports
  "written or episodes" (`:176`). `tests/test_sim_agent_adapter.py:126-140` still expects the
  old shape: **1 failed, 99 passed** on this tree. Given ENG-01 both are always False in a
  real run.

### ENG-14 — Memory-stream plans/reflections depend on thinking deltas that Kimi may not emit (P2, seam, S) — *inferred*

- `memory_stream.py:165-166` accumulates `ThinkingDelta` only; the broker emits
  `thinking_delta` only for `reasoningContent` (`translate.py:590-600`). Run 23 shows
  `</think>` inside *text* (`run-23-report.md:882,6949`), i.e. reasoning arriving as text,
  so `reasoning_chunks` is empty and only `observation` records are written. Also
  call/result pairing is by tool name (`:167-181`), mis-pairing parallel same-name calls.
- Fix: also scan `TextDelta` up to the first tool call; pair on `tool_use_id` (needs the
  `ToolExecution` event to carry it — engine finding) or on order.

### ENG-15 — `search_memory` silently returns nothing for most natural-language queries (P2, seam, S)

- `memory_query.py:107` → `Search.unified` → `signals()` → `search_fts` passes the raw
  string to FTS5 `MATCH` (`signals.py:137-146`); apostrophes, colons and stray quotes are
  syntax errors; `search.py:118-121` swallows them → `[]` → "Nothing in memory matches that."
- Fix: tokenise and double-quote each term in `memory_query.py` before calling.

### ENG-16 — Location-reduced schemas are defeated by herald's tool cache (P1, seam, S)

- `world_tools.py:303-311` replaces ToolDefs in place each tick; `provider.get_cached_tools`
  (`provider.py:520-531`) returns the cached wire array whenever the **name set** is unchanged.
  Descriptions/schemas change, names do not → stale schemas on the wire; the 13% saving in
  `docs/STAKES_TIME_COMPOUNDING.md` §5 was measured on chars, not on requests.
- Fix (seam): after a forced refresh, reset `primary_adapter.cached_tool_names = None` (the
  wrapped provider is reachable via `_ResidentVoiceAdapter._inner`). Engine finding: cache key
  should hash the schema.

### ENG-17 — Learning batch runs inline before the turn, inside the dispatch budget (P2, seam, S)

- `agent_adapter.py:141-142` awaits `learning_gate.on_tick(False)` (5-8 model calls, up to
  108K tokens each) before `send_tick`; it counts against the same 120 s and the same
  Kimi lane. Fix: run it as a background task after the turn; classify it via a request
  header once `herald` exposes one (engine finding: no way to set `X-Kasukabe-Purpose`;
  the broker infers it, `broker/purpose.py:1-40`).

### ENG-18 — Corrections are recorded for 5xx and world outages as if the town refused (P3, seam, S)

- `world_tools.py:193-215` records any `status >= 400`; a world 500 becomes "the world refused
  `harvest`…" in the resident's learning store. Fix: record only 4xx with an `error` body.

### ENG-19 — Per-tick full registry refetch by every engine (P3, seam/world, S)

- With `LOCAL_TOOL_SCHEMAS` on, `refresh_world_tool_registry(force=True)` (`pinned_boot.py:200-205`)
  GETs the full 53 KB registry every tick per engine and rebuilds 76 ToolDefs. Fine at 20,
  wasteful at 250. Fix: have the world return an ETag keyed on (version, location, seat).

### ENG-20 — `_tighten_compaction` writes a dead knob (P3, seam, S)

- `pinned_boot.py:297-336` acknowledged dead (`needs_compaction` reads the module constant).
  Remove; it misleads readers into thinking the threshold is 0.12.

### ENG-21 — MAX_TOKENS continuations consume the call budget and the floor of 2 cannot act (P2, seam/world, S)

- A monologue that hits 1536 → continuation → second call → budget 2 exhausted →
  `exit_reason=max_turns` → next perception says "You ran out of energy for thinking last
  turn after 2 steps" (`call_budget.py:138-146`) though nothing was attempted. Fix: cap
  continuations at 0 in the sim (seam adapter returns `end_turn` on `max_tokens` when no
  tool call was in flight) and let the world's floor mean two *useful* calls.

### ENG-22 — Struggle counters persist across ticks in WM (P2, engine-finding, S)

- `thread.py:3399-3406,4436-4443`: `recent_tool_sigs` and `turns_without_text` carry across
  ticks for a pinned thread, so ENG-07(b) fires on cumulative behaviour, not on one turn.
  Seam mitigation: write a zeroed `{"_struggle": …}` `SYSTEM_EVENT` at `send_tick` start
  (WM is the seam's `working_memory` object; a write is composition, not an engine edit).

### ENG-23 — Post-turn summary subject is the first 40 chars of the perception (P3, engine-finding)

- `thread.py:4277`: subjects like "It is 09:10 on day 0. You are at th" drive the knowledge
  clustering key (`pipeline.py:483-492`). The seam already rewrites the summary prompt;
  it cannot change the subject without an engine change.

### ENG-24 — No goal / need / belief representation (P1, world + seam, M/L)

- See §5.3 for the design. Existing pieces: `Intention` (`world/tools/models.py:125-139`),
  `world.intentions` + `drop/fulfil/expire_intentions` (`state.py:832-870`),
  `format_intention` (`retrieval_depth.py:433-465`), `keep_doing` (`living.py:374-399`),
  memory-stream `plan` records, `memory_query`.

### ENG-25 — Law 1 has already been breached, without a manifest (P2, governance, S)

- `git log -- engine/src/{engine_core,herald,memory,learning_centre}` shows two commits after
  vendoring (`0cb1115` "Run natively on Windows, bound the prompts…"); markers "removed for the
  Kasukabe build" (`thread.py:2760,3258,3797`, `coordinator.py:271,485`, `blocks.py:96`,
  `model_validation.py:30`) and "AUTHORISED LAW 1 EXCEPTION" (`pipeline.py:180,282`). The
  research claim "results are a statement about the product" needs a diff manifest against
  the upstream engine commit, kept in `engine/implementation-notes.md`.

### ENG-26 — Tests that cannot catch the P0s (P2, seam tests, S)

- `tests/test_sim_pinned_boot.py` and `test_sim_learning_gate.py` use fakes for the pipeline
  and never assert a durable memory row; `test_sim_fake_broker.py` asserts the model id on
  the wire but not the request shape (tools count, prompt markers, input size). Add: (1) boot
  + fake broker + `run_batch` → rows in `episodes`/`knowledge_store`; (2) wire assertion:
  tools ≤ N, no assistant markers, input chars ≤ budget; (3) a test that `refresh` with
  changed descriptions changes the wire (ENG-16).

### ENG-27 — Prompt-cache economics are undone every call (P2, seam, S)

- Persona block carries `cache_control` ttl 1h (`provider.py:479-486`), tools carry one
  (`:502-506`), but ENG-02 rewrites the message prefix and invalidates the section cache
  every call, and Bedrock Converse via the broker may not honour `cache_control` at all
  (broker translation drops it — `translate.py` builds Converse blocks without cache
  points). Verify `cache_read_input_tokens` in `llm_calls`; if always 0, the STABLE/DYNAMIC
  split buys nothing today and cost work should target raw size (§6 item 2).

---

## 5. Gaps vs DESIGN.md (in this scope)

| DESIGN.md | State |
|---|---|
| §1 "Agents sleep ~8 game-hours (no interactive LLM calls; learning pipeline runs then)" | Sleep = not daytime (`clock.py:63`); learning gate exists but writes nothing (ENG-01); never reached a night. |
| §3 "Agents must be able to see all of this… what any action will cost" | The energy line is in the perception (A6); per-action cost quoting exists as a tool; **no goals/needs** are shown (ENG-24). |
| §4 "One pinned thread; coordinator/ThreadRouter bypassed" | Done (`pinned_boot.py:27-51`). |
| §4 "DeepSeek carries background/bulk work: learning centre, dream probes, importance scoring, summarizer" | Engine sends no purpose header (no call site in `engine/src`); broker infers from prompt text (`broker/purpose.py`); LC and summariser run on the cognition adapter (`pinned_boot.py:382-383`, single-model `model_profile.py`). Built differently: the seam pins **one** model for everything, contradicting §4's two-model design. |
| §4 "Learning centre … scheduled during sleep ticks" | Also every 18 waking ticks (`config.py:142`), inline before the turn (ENG-17). |
| §4 "Skills: the learning centre authors skill artifacts (distilled procedures) that load on matching tasks. Skill = cheaper cognition" | Not built (ENG-05/06). Only the world's proficiency discount (sim currency) exists. |
| §4 "Personas … memory takes over and they diverge" | Persona delivered (STABLE). Divergence impossible: no soul/social store (ENG-01). |
| §9 "trace records which lane and model served every call" | Broker side, out of scope; engine sends nothing to help. |
| §10 dream probes, ablation arms | `no_learning` arm honoured by the gate (`learning_gate.py:76-95`); nothing else. |
| ROADMAP T1 DYNAMIC layer (evolved soul + social graph) | Never renders: `ConsciousnessManager()` built with no stores (`pinned_boot.py:485`). |
| ROADMAP T4 "emit each [refusal] as a LearningSignal… in the shape the pipeline already reads" | Emitted (`world_corrections.py`), but in a shape step 11 does not read (ENG-05) and into a pipeline that cannot write (ENG-01). |
| ROADMAP T8 "compaction trigger on the wallet budget; p95 ≤ 4,000" | Window lowered instead; produced ENG-02. p95 is 15-20K. |

---

## 5b. Gaps vs the target

| Target | What exists to build on | What to add | Where |
|---|---|---|---|
| Thriving, not surviving (every resident earns, eats, sleeps, learns, gets better) | Perception energy line; call budget; `keep_doing`; corrections written | Fix ENG-01/02/03 so a tick costs ≤ $0.003 and a night consolidates; goals model so "getting better" has a referent | seam (`pinned_boot.py`), world (`retrieval_depth.py`, `call_budget.py`) |
| Organisations and government through tools | 76 tools, seat gating (registry) | Nothing in the mind knows a resident holds a seat or a job beyond the standing brief; serve seat/role-relevant schemas (ENG-03) and a role goal ("as minister, this week: …") from the goals model | world/registry + seam prompt |
| Expansion and construction by the town's builders | `build_structure` tool | Out of this scope; the mind needs multi-tick projects = goals with progress (§5.3) | world |
| Skills/tools that cut model calls | `keep_doing`, proficiency discount, `skillify` workflows in perception | Mastered workflow → runs under `keep_doing` without a model call; workflow as cached DYNAMIC section; retire the engine `user_skills` path (ENG-06) | world `state.py` + seam section |
| Supply/demand, ideology-driven mechanics | price board in perception | Beliefs about prices/people (§5.3) so residents act on remembered prices rather than re-asking; nothing else in this scope | world + seam |
| Evolving goals | `set_intention`, `keep_doing`, memory-stream plans | §5.3 | world models/state/tools, `retrieval_depth.py`, seam reflection at sleep |
| World-class instrumentation | session log, memory stream, trace store, `llm_calls` | Log the *goal ids and belief ids rendered* into each turn's session-log input; record compaction strategy per call (engine emits `CompactionEvent` — subscribe in the seam and write it to the session log); record Kimi-markup recoveries | seam `session_log.py` (subscribe to `CompactionEvent`, `StrugglingDetected`) |

### 5.3 Design — evolving goals that fit the seam

**Principle.** Goals are world state (the world owns state and the trace), rendered into
the VOLATILE layer (the perception) by the world, revised by the resident through tools
and, once a night, by a reflection call the *engine* makes (CONVENTIONS: the world never
calls the broker on an agent's behalf). Nothing in `engine_core` changes.

**Data model** (`app/world/tools/models.py`):

```
Need    { kind: food|energy|money|rest|shelter|social|safety, level: 0..1, since_tick, source: "world" }
Goal    { id, agent_id, text (≤160), horizon: now|today|week|life, priority: 1..5,
          progress: 0..1, status: active|done|abandoned|superseded|expired,
          origin: need|persona|social|refusal|reflection|order, created_tick, deadline_tick?,
          parent_id?, done_when: {tool?, item?, sim_amount?}, evidence: [event_id...] }
Belief  { id, agent_id, subject (place|person|price|rule|self), statement (≤160),
          confidence: 0..1, evidence: [event_id...], last_updated_tick, contradicted_by?: id }
```

Persona seeds the `life`/`week` goals at genesis (ambitions, fears, secret hope are already
in `RichGenesisAgent`); `Intention` becomes a `Goal(horizon=now)`.

**Update triggers** (all deterministic, no model call, in `world/tools/state.py` and the tick
loop):

| Trigger | Effect |
|---|---|
| Perception build | Needs recomputed from hunger, wallet runway, sleep debt, unanswered addressed questions (social). A need ≥0.7 with no active goal of that kind spawns `Goal(origin=need, horizon=now)`. |
| Tool outcome | `done_when` matched → `progress=1, status=done`; partial (e.g. sim earned toward a target) → progress. |
| Refusal (correction) | `Belief(subject=place|rule, "cannot X at Y: <reason>", confidence += 0.3)`; two refusals of the same goal → `priority -= 1`. |
| Hunger/wallet | `need.level` and, below thresholds, a forced `now` goal ("eat", "earn/buy energy") — a fact, not advice: the line reads "You are hungry (0.8)" and the goal reads in the resident's own words once it sets it. |
| Social events | A request addressed by name creates a candidate goal the resident may accept via `set_goal` (never auto-accepted: measuring compliance is the point). |
| Day end | `today` goals expire → `expired`, outcome line rendered once (existing `intention_outcome` path). |
| Sleep reflection | The engine seam (`engine/src/sim/goal_reflection.py`, called from the sleep branch of `/tick` next to the learning gate) reads the day's session log + the world's goals (`GET /agents/{id}/goals`) and asks the LC adapter one question in the resident's voice: "which of these did you make progress on, which do you drop, what do you now believe". It POSTs `revise_goals` — a world tool under the resident's id, so it is traced like any act. One call per resident per night. |

**Reaching the prompt.** `retrieval_depth.render_perception_text` gains a
"What you are trying to do" block right after the intention line: top-2 needs (level ≥0.5),
≤3 active goals sorted by priority (`[week] rebuild the mill roof — 2/5 planks bought`),
≤3 beliefs relevant to the current place/people. Facts only, no verbs; ≈80 tokens.
Tools: `set_goal`, `update_goal(progress|priority|status)`, `retire_goal`, `note_belief`,
`revise_goals(batch)` in `app/world/tools/primitives/goals.py` (replacing `intent.py`).

**Retirement/replacement.** Cap 3 active per horizon; a new goal beyond the cap supersedes
the lowest-priority one of the same horizon (recorded, not silent). Expiry by deadline.
`abandoned` only by the resident (tool) or reflection.

**Tracing.** Events `agent.goal.set|progressed|retired|superseded`, `agent.belief.updated`
→ `TraceStore` tables `goals`, `beliefs` (`app/supabase/migrations`, `app/world/wiring.py`).
The rendered goal/belief ids go into the perception's trailing HTML comment
(`<!-- goals: g12,g15; beliefs: b3 -->`) so the session log's `input` carries them and every
decision can be joined to what the resident was trying to do. Metrics: goals set/done/expired
per resident-day; mean ticks to done by horizon; belief accuracy vs ground truth (prices,
places).

**Files that change.** `app/world/tools/models.py`, `app/world/tools/state.py`,
`app/world/tools/primitives/goals.py` (new; `intent.py` folded in), `app/world/tools/bootstrap.py`
(register), `app/world/ledger/action_costs.py` (rows), `app/world/retrieval_depth.py`
(`format_goals`), `app/world/run_genesis.py` (goal lines, `GET /agents/{id}/goals`, day-end
expiry), `app/world/wiring.py` + migration (trace), `engine/src/sim/goal_reflection.py` (new),
`engine/src/sim/agent_adapter.py` (sleep branch), `engine/src/sim/session_log.py` (nothing —
ids ride in the input). Tests: `world/tools/tests/test_goals.py`, `engine/tests/test_sim_goal_reflection.py`.

---

## 6. Recommended work items (P0 first)

1. **Wire the durable stores (ENG-01).** *Spec:* in `pinned_boot.py` construct
   `EpisodicMemory/KnowledgeStore/SocialGraph(db, None)` and `Soul(db)`; pass to
   `MemorySystem` and `ConsciousnessManager`; `LCPipeline(on_data_changed=invalidate)`; add
   the DYNAMIC section render (`consciousness.render_context` shape, in the resident's voice).
   *Acceptance:* boot with the fake broker returning canned JSON; `run_batch` → ≥1 row in
   `episodes`, `knowledge_store`, `social_graph_nodes`; a second batch with the same signals
   → `delete_processed` ran and `learning_signals` is empty; the next turn's system prompt
   contains the new knowledge line. Effort S/M.
2. **Stop the per-call emergency compaction and bound history in the seam (ENG-02/09).**
   *Spec:* `DISABLE_AUTO_COMPACT=1` in `raw_process_env`; seam `ConversationStore` view
   (last N=3 exchanges as action records + rolling resident-voice summary); raise
   `KASUKABE_CONTEXT_WINDOW` to 128K (so caps stop truncating). *Acceptance:* fake-broker
   test asserts no request contains `[Context restored after compaction]` or
   `compaction boundary`; after 30 ticks the request's message list is ≤ 8 messages;
   `tokens_before/after` `CompactionEvent` count is 0. Effort M.
3. **Cut tool tokens to ≤4K on the wire (ENG-03/16).** *Spec:* world serves ≤20 full schemas
   by (location, role, seat) + one-line index; strip price prose; seam resets herald's tool
   cache on refresh. *Acceptance:* `llm_calls.input_tokens` p95 ≤ 8K over a rung-1 run;
   a resident at the bakery sees `sell` in full and `arrest` as one line; the wire array
   changes when the resident walks (test on `provider.get_cached_tools`). Effort M.
4. **Signal watermark (ENG-04).** *Spec:* seam `SignalStore` subclass with per-thread flush
   watermark and a 500-row cap per batch; installed on the coordinator before thread creation.
   *Acceptance:* after 20 turns, `learning_signals` rows = O(turn events), not O(n²);
   largest LC request ≤ 15K tokens. Effort S.
5. **Strip the scaffolding on the wire (ENG-07).** *Spec:* extend `_ResidentVoiceAdapter` to
   `stream_with_tools`; drop the complexity nudge and struggle injection; rewrite the
   continuation prompt; zero the struggle state at tick start. *Acceptance:* character gate
   P1-P3 (`world/evals/character.py`) over 500 sampled requests: zero hits for
   `request_plan`, `Execution was struggling`, `Files Touched`, `[user]`, `September`. Effort S/M.
6. **Parse Kimi markup in the broker (ENG-08) and accept `world_` names in the registry.**
   *Acceptance:* a canned Converse text stream containing the run-23 markup yields a
   `tool_use` SSE block named `world_say` with the parsed JSON, and the trace shows the tool
   invocation; `set_intention(done_when_tool="world_cook")` is accepted. Effort S.
7. **Namespace backend contract (ENG-11) and deadline cancel (ENG-12).** *Acceptance:*
   `--supervisor-backend namespace` completes a 10-tick mock run with `steps_used` populated
   and the learning gate called; a turn exceeding the deadline returns a partial result and
   the engine loop exits within one call. Effort S.
8. **Goals model (ENG-24, §5.3).** *Acceptance:* a hungry resident's perception shows a
   `food` need and, after `set_goal`, the goal line; a `buy`+`eat` marks it done and the
   outcome line renders once; `goals` trace table has one row per transition; sleep
   reflection posts `revise_goals` once per resident per night on the LC adapter. Effort M/L.
9. **World-as-corrector in the seam (ENG-05).** *Spec:* per-tool refusal ledger rendered as
   beliefs (item 8) and a DYNAMIC "what I have learned" section; drop reliance on step 11.
   *Acceptance:* after two refusals of `craft` at the bakery, the next prompt carries the
   belief and the third attempt does not occur in ≥80% of L1 scenarios. Effort S.
10. **Fix the red sim test and add wire-shape tests (ENG-13/26).** Effort S.
11. **Learning batch off the tick's critical path (ENG-17); 4xx-only corrections (ENG-18);
    FTS escaping (ENG-15); remove dead knob (ENG-20); continuation cap (ENG-21).** Effort S each.
12. **Law 1 manifest (ENG-25).** Effort S.

---

## 7. Open questions for the operator

1. Is the single-model rule in `model_profile.py` (Kimi for everything, including the
   learning centre and summariser) still the decision, given DESIGN.md §4's two-lane design
   and the broker's inferred-purpose routing? If DeepSeek is allowed for LC, the seam's
   `lc_adapter` is the one-line place to change it.
2. Do you want history bounded by the seam (item 2) or the engine's compaction kept and the
   window raised to ~60K so the engine's LLM summariser (`COMPACT_PROMPT`) starts running?
   The former is cheaper and stays in the resident's voice; the latter keeps more engine
   behaviour "as shipped" but costs one extra 20-40K call per compaction and needs a
   rewrite of `COMPACT_PROMPT` in `resident_voice.py`.
3. Should goals live in the world (traced, deterministic triggers) as proposed, or in the
   engine's knowledge store (LC-written, fuzzier, no trace table)? The proposal assumes the
   world.
4. Is the broker the right home for Kimi-markup recovery (it changes what the trace records
   as a tool call), or do you want the raw text preserved in `llm_calls` and the recovery
   done in the seam?
5. Is a Law 1 manifest acceptable as the governance answer to ENG-25, or do you want the
   two post-vendoring engine commits reverted and re-expressed in the seam?
6. The `no_learning` arm now also disables the goal reflection if it is implemented in the
   sleep branch — intended?

---

## 8. Test run

- `cd engine && uv run --extra dev pytest tests/test_sim_*.py -q` → **1 failed, 99 passed
  (2.63 s)**: `test_sim_agent_adapter.py::test_the_learn_route_consolidates_on_demand`
  (expects `learning_ran: True`, gets `False` with `knowledge_written: 0` — ENG-13).
- `uv run --extra dev pytest tests -q -x` → **751 passed, then 1 failed**:
  `test_command_semantics.py::TestEndToEndSemantics::test_grep_match_through_run_bash`
  (bubblewrap sandbox not available; documented as environment-dependent in
  `docs/SELF_AUTHORING_AGENTS.md` §"Lint, and the engine's excluded tests").
- The full suite without `-x` was started under a 10-minute cap and had not finished when
  this report was written (progress log at `scratchpad/engine-pytest-full.log`; the
  `test_lc_scheduler_pause`/`test_integration_boot` family is known to hang outside Docker).
- Plain `uv run pytest` fails at `tests/conftest.py:129` (`pytest_asyncio` missing): the
  default env does not install the `dev` extra; the brief's command needs `--extra dev`.
