*Part of the codebase review set in `docs/review/` (see `00-README.md`). Written 2026-09-03 against `Real` at `5400e94` by reading the code; every claim cites `path:line` relative to `app/` unless stated. Finding ids (`INFRA-nn`) are referenced from the cross-cutting documents.*

# INFRA review: the money pipe and the record

Scope: `app/broker/**`, `app/supabase/**`, `app/research/**`, `app/infra/**`, `infra/*.workflow.js`, `app/Dockerfile`, `app/docker-compose.yml`, `app/start-genesis.sh`, `app/Makefile`, `app/pyproject.toml`, plus the world-side call sites the broker depends on (`world/run_genesis.py` embedded ledger routes and persistence, `world/main.py` ledger routes, `world/wiring.py` TraceStore) and the engine seam files that set the broker contract (`engine/src/sim/config.py`, `model_profile.py`, `pinned_boot.py`, `agent_adapter.py`). Read-only review against `Real` at `5400e94`. Every claim is cited `path:line`; inferences are marked.

Test run (allowed by the brief): `cd app && uv run pytest broker supabase research tests -q -x` **fails at collection** on this host, before a single test runs (`broker/tests/test_broker_app.py:12` imports `broker.app`, whose module-level `app = create_app()` at `broker/app.py:335` opens `/data/kasukabe.db` in real mode and dies with `sqlite3.OperationalError: unable to open database file`). With `TRACE_SQLITE_PATH` pointed at a writable file and without `-x`: **534 passed, 11 failed, 7 skipped in 14 s**. The 11 failures are environmental: 9 in `research/tests/test_research_parquet_export.py` (DuckDB tries to download the `sqlite_scanner` extension at first use and gets HTTP 403 through this host's proxy), 1 in `research/tests/test_demographics.py::test_it_measures_a_real_genesis_town` and 1 in `tests/test_console_world_integration.py` (both need `smallville/` Ville assets, absent from this checkout). The 7 skips are the Postgres live-migration tests (no `psql`), the crash-safety SIGKILL test on this platform, and the real-hardware Firecracker test. See INFRA-11 and INFRA-30.

---

## 1. Inventory

Status legend: LIVE = on the production run path from `world.run_genesis` (`start-genesis.sh` boots `broker.app:app` then `world.run_genesis --live --world-mode embedded`); TEST-ONLY = reached only by tests; ORPHANED = no production caller.

### 1.1 `app/broker/`

| Path | Lines | Purpose | Status | Proof |
|---|---|---|---|---|
| `broker/app.py` | 335 | FastAPI entry: `/health`, `/admin/lanes`, `/admin/trace`, `/admin/spend`, `POST /v1/messages`; `build_service` wires real/mock | LIVE | `start-genesis.sh:27` runs `uvicorn broker.app:app`; module-level `app = create_app()` at `app.py:335` |
| `broker/service.py` | 860 | `BrokerService` pipeline: auth, purpose, allowlist, spend cap, lane, fit, translate, wallet check, bucket, dispatch, settle, trace | LIVE | `app.py:113`, called from `messages()` at `app.py:301`/`app.py:318` |
| `broker/translate.py` | 797 | Anthropic wire <-> Bedrock converse, `legalise_transcript`, `tools_referenced_in`, streaming SSE translation incl. `reasoningContent` | LIVE | imported `service.py:102-107` |
| `broker/lanes.py` | 449 | six-lane pool, per-lane `CircuitBreaker`, `classify_purpose`, `derive_purpose`, `build_lane_pool` | LIVE | `app.py:116` |
| `broker/rate_limit.py` | 141 | `TokenBucket` (LIVE, `lanes.py:421`); `RateLimiterRegistry` | LIVE / **ORPHANED** | `RateLimiterRegistry` is constructed only in `broker/tests/test_rate_limit.py:136-160`; `rate_limit.py:20-27` admits it |
| `broker/allowlist.py` | 30 | exact-match model allowlist | LIVE | `service.py:453` |
| `broker/ledger.py` | 304 | `HttpLedgerClient` (LIVE, `app.py:102`), `InMemoryLedgerClient` (mock/test) | LIVE / TEST-ONLY | `app.py:81` only when `mock_mode` |
| `broker/spend.py` | 133 | process-wide USD governor, real usage only | LIVE | `app.py:117`, `service.py:405-426` |
| `broker/pricing.py` | 166 | nano-dollar exact pricing, `CostAccountant` carried remainder | LIVE | `service.py:535,661,665` |
| `broker/purpose.py` | 186 | `infer_purpose`: marker-matched background detection, else `derive_purpose` shape tag | LIVE | `service.py:439` |
| `broker/trace.py` | 648 | `TraceRecord`/`BrokerEvent`, `BatchWriter`, `SqliteTraceSink` (LIVE), `SupabaseRestSink` (**ORPHANED**), `capped_body` | LIVE | `app.py:110-111`; `SupabaseRestSink` constructed only in `broker/tests/test_trace.py:149-170,315` |
| `broker/tokens.py` | 41 | ~4 chars/token estimator | LIVE | `service.py:526`, `fit.py:86` |
| `broker/fit.py` | 137 | context-window fit: clamp `max_tokens` or refuse | LIVE | `service.py:485` |
| `broker/bedrock.py` | 293 | `Boto3BedrockGateway` (LIVE), `MockBedrockGateway` (mock/test) | LIVE | `app.py:95` |
| `broker/auth.py` | 95 | bearer token -> agent id, optional HMAC | LIVE | `service.py:429` |
| `broker/errors.py` | 164 | error hierarchy with `status_code`, `failed_at` | LIVE | everywhere |
| `broker/models.py` | 85 | `MessagesRequest` (extra=allow) | LIVE | `service.py:445` |
| `broker/config.py` | 525 | `BrokerSettings`, law constants, `export_aws_env`, inference-profile mapping | LIVE | `app.py:179` |
| `broker/preflight.py` | 227 | operator CLI: credentials/profiles/one canary invoke | TOOL (operator-run) | `python -m broker.preflight`; not imported by the run path |
| `broker/discover_profiles.py` | 184 | operator CLI: list inference profiles per lane | TOOL | `python -m broker.discover_profiles` |
| `broker/README.md` | 96 | package doc | DOC, **stale** (says `MOCK_MODE=true` is the default at lines 12-13, 64, 94; `config.py:259,431-439` now rejects `MOCK_MODE` outright) | |
| `broker/tests/*` (24 files, 346 tests) | ~5,000 | see section 7 | TEST | |

### 1.2 `app/supabase/`

| Path | Lines | Purpose | Status | Proof |
|---|---|---|---|---|
| `supabase/sqlite_client.py` | 625 | the one `SupabaseClient` impl: WAL pragmas, `ensure_schema` (additive `ALTER`), coercion tables, `conn_lock`, local/S3 archive | LIVE | broker `app.py:109`; world `run_genesis.py:3249` via `factory.get_client` |
| `supabase/sqlite_schema.sql` | 632 | the live schema, 16 tables | LIVE | applied by `sqlite_client.py:404` |
| `supabase/schema_gen.py` | 412 | generates `llm_calls`/`events` DDL from `TraceRecord`/`BrokerEvent` | TEST-ONLY (guard) | only `supabase/tests/test_schema_gen.py` imports it; `__main__` is a paste aid (`schema_gen.py:400-413`) |
| `supabase/client.py` | 190 | ABC + PostgREST-style filter helpers | LIVE | base of `SqliteClient` |
| `supabase/config.py` | 145 | `SupabaseConfig.from_env` (`KASUKABE_SQLITE_PATH`, S3 knobs) | LIVE | `run_genesis.py:3246`, `factory.py:28` |
| `supabase/factory.py` | 29 | `get_client()` | LIVE | `run_genesis.py:1924,3249`; `wiring.py:1418` |
| `supabase/archiver.py` | 126 | gzip+sha256+upload; `archive_payload` (LLM payloads) has **no caller** | LIVE for checkpoints only | `run_genesis.py:1926-1930` (`archive_bytes`); `wiring.py:564,1077`; grep of `broker/` finds no `PayloadArchiver` use |
| `supabase/s3_client.py` | 316 | boto3-free SigV4 PUT, IMDSv2 | LIVE-but-never-exercised | reached only when `KASUKABE_S3_BUCKET` is set (`sqlite_client.py:598`); `start-genesis.sh` never sets it |
| `supabase/repair_live_schema.py` | 180 | stdlib repair tool for the 2026-07-31 incident | TOOL | manual |
| `supabase/migrations/*.sql` (22 files) | ~1,200 | **dormant** Postgres DDL, still extended (`20260825`, `20260902`, `20260903`) | ORPHANED (docs say dormant, `README.md:32-33`) | nothing applies them except the Postgres-only test that skips |
| `supabase/README.md` | 252 | package doc | DOC | |
| `supabase/tests/*` (9 files, 137 tests) | ~2,600 | see section 7 | TEST | |

### 1.3 `app/research/`

| Path | Lines | Purpose | Status | Proof |
|---|---|---|---|---|
| `research/parquet_export.py` | 188 | per-game-day DuckDB -> Parquet (+ optional S3) | **ORPHANED** | grep for `export_game_day`/`parquet_export` outside tests finds only `research/__init__.py` docstring |
| `research/demographics.py` | 402 | population slices, bias report, emergence check | **ORPHANED** (pure functions; HANDOVER §9 says never run on a real trace) | no import outside `research/tests/` |
| `research/tests/*` | 525 | | TEST | |

### 1.4 `app/infra/` and `infra/`

| Path | Lines | Purpose | Status |
|---|---|---|---|
| `infra/firecracker/{supervisor,vm_manager,api_client,process,resources,tap_provisioner,snapshot_store,config}.py` | ~1,800 | microVM supervisor track | **ORPHANED**: `run_genesis.py:5455-5458` refuses `--supervisor-backend firecracker` ("not wired ... this package must never import infra/"); runner carries its own `ProcessSupervisor` (`run_genesis.py:1001`) and watchdog copy (`run_genesis.py:807-830`) |
| `infra/firecracker/rootfs/*`, `kernel/*`, `network/*` | ~3,900 | rootfs/kernel/bridge build | never run on a KVM host (`firecracker/README.md:20-35`) |
| `infra/firecracker/tests/*` (10 files) | ~1,100 | against a fake Firecracker API | TEST (own compose, not in `app/` suite) |
| `infra/systemd/*.service` (5) | 186 | world/broker/game/console/litestream units | **UNUSABLE as written** (INFRA-04) |
| `infra/env/*.env.example` (6) | 129 | env templates | stale (INFRA-04) |
| `infra/scripts/01..07, teardown.sh` | 733 | EC2/S3/IAM/Caddy bring-up | operator tooling, not exercised in any documented run |
| `infra/litestream.yml` | 79 | WAL -> S3 replication | never exercised (no run report mentions it; RUNBOOK §10 is "once it lands") |
| `infra/Caddyfile`, `infra/RUNBOOK.md`, `infra/README.md` | 532 | reverse proxy + runbook | RUNBOOK "Known gaps" §1 is stale (INFRA-04) |
| `infra/phase2-genesis.workflow.js`, `phase2-verify.workflow.js` | 191 | historical agent-orchestration scripts (round 1) | ORPHANED, historical |
| `infra/kimi-quota-request.md` | 38 | quota request draft (says single-model, 100 RPM; predates six lanes) | DOC, stale |

### 1.5 Build/launch files

| Path | Lines | Purpose | Status |
|---|---|---|---|
| `app/start-genesis.sh` | 51 | the real launcher: docker `broker` then `world python -m world.run_genesis --live --world-mode embedded --supervisor-backend process` | LIVE |
| `app/docker-compose.yml` | 190 | `test`, `world` (`world.main:app`), `broker` (with `MOCK_LEDGER_BALANCE_CENTS`, i.e. mock-mode knobs the broker no longer accepts), `observatory` (`WORLD_BASE_URL=http://genesis:8100` but no `genesis` service exists), `game` | LIVE for `test`/`broker` image; `world` service is not what runs a genesis |
| `app/Dockerfile` | 131 | one image for world+broker+engine deps; pre-fetches the DuckDB sqlite extension | LIVE |
| `app/Makefile` | 19 | dev targets | dev |
| `app/pyproject.toml` | 60 | deps; `boto3`, `duckdb`, `httpx` | LIVE |

---

## 2. How it works today

### 2.1 One model call, end to end

1. **Engine side.** `run_genesis.GenesisRunner.start_agents` spawns one `python -m src.sim.sim_main` per resident with `BROKER_BASE_URL`, `BROKER_API_KEY=sign_broker_token(agent_id, ...)` (`run_genesis.py:3974-3975`), and `CDT_URL`/`CDT_KEY` set by `ProcessSupervisor.start` (`run_genesis.py:1085-1086`). The token is `<agent_id>.<hmac>` when `AUTH_SHARED_SECRET` is in the runner's env, else the bare `agent_id` (`run_genesis.py:257-291`). `sim/config.py:210-228` maps that to herald's `CDT_URL`/`CDT_KEY`, pins every model role to `moonshotai.kimi-k2.5` (`model_profile.py:466`) and sets `MAX_TOKENS=1536` (`sim/config.py:119,223`). Compaction fires at 80% of `SIM_CONTEXT_WINDOW=12,500` tokens, i.e. ~10,000 (`model_profile.py:545-567`). Each awake tick the runner POSTs `/tick` with a `call_budget` from `world/call_budget.py::budget_for` (floor 2, ceiling 12, 5% of wallet at 0.75 cents/call: `call_budget.py:43-70`, `run_genesis.py:4454`); the engine passes it as `RequestOptions(max_turns=...)` (`pinned_boot.py:253-256`). The pinned thread streams (`thread.py:3815` `stream_with_tools`) via herald's httpx transport to `CDT_URL/v1/messages` with `Authorization: Bearer <token>` and no `X-Kasukabe-Purpose` header (nothing in `engine/src/sim/` or `learning_centre/` sets one; grep empty).

2. **Broker front door.** `broker/app.py:279-304`: `POST /v1/messages` first checks both `BatchWriter`s for `is_halted` (`app.py:138-163`; 3 consecutive failed flushes -> 503 `overloaded_error`), parses JSON, and for `stream: true` calls `stream_messages_response` (`app.py:309-332`) which awaits the first SSE event eagerly so pre-dispatch refusals still get a real HTTP status.

3. **`prepare_call`** (`service.py:428-592`), in order: `resolve_agent_id` (`auth.py:64-95`; unsigned tokens accepted when the secret is empty), `infer_purpose` (`purpose.py:142-186`: if no header, scan system prompt and last user message for learning-centre / summariser markers **only when the body carries no tools**, else `derive_purpose` shape tag `auto:agent_turn`/`auto:other`), `classify_purpose` (`lanes.py:137-158`, strips `auto:`; prefixes `learning_centre`/`dream_probe`/`importance_scoring`/`summarizer` -> BACKGROUND), pydantic validate, `check_model_allowed` (`allowlist.py:18-30`), `SpendGovernor.should_block_dispatch` (429 when HALTED, `service.py:458-465`), `LanePool.select` (`lanes.py:335-381`: cognition scans Kimi lanes then DeepSeek for a bucket with a token now, else falls back to the first non-broken lane in preference order and waits on its FIFO; background scans DeepSeek only; all-open -> 529), `fit_to_context_window` (`fit.py:93-137`: whole body serialised /4 chars, 2,000-token margin; clamps `max_tokens` down to no less than 256 or refuses), `build_converse_request` (`translate.py:369-453`: `legalise_transcript` rewrites orphan `tool_result`s as text and merges same-role turns, `translate.py:185-249`; drops `thinking` blocks; when no tools are offered but history has `tool_use`, replays minimal `toolSpec`s so Bedrock's `toolConfig` validation passes, `translate.py:424-444,456-482`; `thinking` -> `reasoning_config` clamped to half of `max_tokens`, `translate.py:337-366`), then the **pre-dispatch wallet check**: `estimate_tokens("x"*request_text_chars(req))` counts only text blocks (`service.py:122-157`, tool schemas and JSON inputs excluded by design) plus `max_tokens` as the output ceiling, priced on the **serving** lane's model, ceiling-rounded to cents (`pricing.py:100-113`); `GET {world}/ledger/payer/{agent_id}` (`ledger.py:129-140`) returns the resolved payer (parent for minors, `world/ledger/core.py:453-463`) and balance; `balance_cents < estimate_cents` -> 402 `InsufficientFundsError` recorded as a trace row and a `broker.call.rejected` event (`service.py:550-567,594-639`). Finally `await lane.bucket.acquire()` (`rate_limit.py:89-109`: lock held for the whole wait, strict FIFO, 100 tokens/min per lane) and a `broker.rpm.throttled` event if it waited.

4. **Dispatch.** `Boto3BedrockGateway.converse_stream` (`bedrock.py:158-182`): `modelId` swapped for the lane's inference-profile ARN if configured, botocore retries disabled (`max_attempts=1`, `bedrock.py:113-130`), each stream event pulled via `asyncio.to_thread(next, stream)`; throttling codes -> `UpstreamOverloadedError` (529), everything else -> `UpstreamError` (502).

5. **SSE back.** `anthropic_sse_events_from_converse_stream` (`translate.py:629-788`) emits `message_start` with the **estimated** input tokens, a synthetic `content_block_start` for any delta whose index never started (`translate.py:724-732`; the D1 fix), `text_delta`/`input_json_delta`/`thinking_delta`/`signature_delta`/`redacted_thinking_delta`, `content_block_stop` only for started indices, `message_delta` with the stop reason, and calls `on_usage` with the exact usage from the terminal `metadata` event. `StreamAccumulator.ingest` (`service.py:182-291`) mirrors it for the trace and tolerates unstarted deltas (`service.py:236-246`).

6. **Settle** (`service.py:641-767`): `exact_cost_nano_dollars(model, input, output)` from Bedrock's own counts (`pricing.py:94-97`); `record_spend` folds it into the governor and emits `broker.spend.warned`/`system.sim.halted` once per transition (`service.py:395-426`); `CostAccountant.debit_cents` converts to whole cents with a per-payer carried remainder (`pricing.py:136-143`); `POST {world}/ledger/debit` (`ledger.py:142-166`). On the embedded world app that is `run_genesis.py:2892-2916` -> `ledger.burn_for_inference(agent_id, cents, world_state.current_tick, memo=purpose)` -> `debit_agent_usd` -> `resolve_usd_payer` -> `move(payer -> BURNED_ACCOUNT)` with the $1000 invariant checked (`world/ledger/core.py:453-577`). 402 from the ledger (race) is caught, the cents are credited back to the in-process remainder, and the response still returns (`service.py:678-696`). `reconcile_usage_and_content` (`service.py:300-343`) flags billed-but-empty bodies into `service.content_loss` and a `broker.content.lost` event. Then one `TraceRecord` (`llm_calls`) and one `BrokerEvent` (`events`) are buffered into `BatchWriter`s (batch 20 / 2 s, `config.py:280-281`) and flushed through `SqliteTraceSink.write_batch` (`trace.py:328-332`), which first checks the store's inode has not changed (`trace.py:281-319`), then `SqliteClient.insert` under `conn_lock` (`sqlite_client.py:488-515`); bodies are capped at 64 KB into a still-valid object (`trace.py:497-603`).

7. **What the world does with the burn.** On `world/main.py` the debit route calls `server.burn_inference`, which marks the payer dormant at <= 0 (`wiring.py:1190-1207`). On the **embedded** app used by every real run there is no such hook (`run_genesis.py:2892-2916`); dormancy is instead swept **every tick** by `mark_the_broke_dormant` (`run_genesis.py:4591,4911-4956`), which also clears the resident's continuing action, publishes the life event with the household and the 24-game-hour deadline, and leaves the resident scheduled (see INFRA-03). Death from unrevived dormancy is checked hourly (`run_genesis.py:4607`).

### 2.2 At the cap

The broker cap (`SPEND_CAP_USD`, `config.py:296`) is computed from real usage so the crossing call is always paid (`spend.py:23-30`); every later call is refused 429 `SpendCapExceededError` **and still writes a trace row + event** (`service.py:458-465`). The runner has a second governor on the ledger's `burned` balance (`run_genesis.py:2077-2097`), aligned with the broker's cap by reading `SPEND_CAP_USD` (`run_genesis.py:651-666`), checked after every tick (`run_genesis.py:5175-5179`); on a tick that lost any dispatch it also polls `GET /admin/spend` and stops on `state == "halted"` (`run_genesis.py:4065-4084,5186-5190`); as a last net, 20 consecutive ticks with zero successful dispatches stop the run (`run_genesis.py:380,5191-5201`). `drain_before_halt` reserves 10 cents of the cap for a learning batch, but it is only called from `run_burst` (`run_genesis.py:5228`), never from `run_forever`, which is the continuous loop; SYSTEM_REVIEW Part 3 says it has never fired.

### 2.3 Where 429/529 go

Bucket saturation never produces a 429: `TokenBucket.acquire` queues (`rate_limit.py:89-109`), so the engine sees latency, which is what hits the runner's 120 s `tick_timeout_s` (`run_genesis.py:566-573`) and discarded 10 paid turns in run 23 (`docs/runs/run-23-report.md:7326`). The only broker-originated 429 is the spend cap. Herald excludes 429/529 from its transport retries (`herald/transport.py:33-35,45-46,61-65`) and hands them to `OverloadTracker` (`herald/retry.py:87-90,145-175`): a 429 resets the 529 counter and is returned to the agent loop; 529s count toward a fallback model the sim never configures (`sim/config.py:219`), so background consumers raise at once and foreground turns end in error. A failed turn surfaces as a 500 from the engine's `/tick` (the route does not catch, `agent_adapter.py:116-151`), which the runner records as a failed dispatch (`run_genesis.py:1363-1372`) and counts toward barren ticks.

### 2.4 The record

`llm_calls` has 24 real columns plus 2 generated: `task_label, id, agent_id, payer_agent_id, model, requested_model, region, call_class, purpose, request_body, response_body, stop_reason, input_tokens, output_tokens, cost_nano_dollars, cents_debited, latency_ms, rate_limit_wait_ms, success, error_type, error_message, failed_at, created_at, tick` (+ `game_day`, `game_minute_of_day` generated from `tick`) (`sqlite_schema.sql:318-360`), generated from `TraceRecord` (`trace.py:83-139`) and pinned by `supabase/tests/test_schema_gen.py:57-69`. Two of those columns are never populated in production: `tick` (no construction site in `service.py` passes it; grep `tick=` in `service.py` is empty; `trace.py:33-46` says who should) and `task_label` (computed post hoc in memory by `world/stakes_report.py:105-147`, never written back).

World-side, a genesis run attaches `attach_persistence` (`run_genesis.py:3186,3203-3300`) which subscribes one recorder to `"*"` and writes `agents` (seed), `businesses` (seed), `utterances` (with `addressed_agent_ids`), `transactions` (with `counterparty_id`), `memories`, `skills`, `tools_registry`; three consecutive rejected writes call `request_stop()` (`run_genesis.py:3283-3296`). It deliberately does **not** attach `record_event` (`run_genesis.py:3217-3224`), so `events` holds only the broker's `broker.*` rows. `world_snapshots` is written by `mirror_checkpoint_to_shared_store` (`run_genesis.py:1909-1966`) on every daily checkpoint, but through `get_client()` with no config (INFRA-01). `relationships`, `organizations`, `petitions`, `cases`, `buildings`, `eval_metrics` have no writer anywhere (grep across `world/` finds no inserts; `wiring.py:552-558` says so for its own scope).

Durability: SQLite WAL + `busy_timeout` + `synchronous=NORMAL` (`sqlite_client.py:123-149`), proven by `test_supabase_crash_safety.py`. Litestream config exists (`infra/litestream.yml`) and expands `KASUKABE_SQLITE_PATH`/`KASUKABE_S3_BUCKET`/`AWS_REGION` from `infra/env/litestream.env`; nothing in `start-genesis.sh` or any run report runs it. Parquet export (`research/parquet_export.py`) is a complete function with no scheduler or caller.

---

## 3. Findings

### INFRA-01 - `world_snapshots` rows and checkpoint archives go to a throwaway in-memory database on every real run
**P0** (corrupts the record) - `world/run_genesis.py:1909-1966`, `supabase/factory.py:21-29`, `supabase/config.py:53,110`, `start-genesis.sh:20,37`.
`mirror_checkpoint_to_shared_store` calls `get_client()` with **no config** (`run_genesis.py:1924`). `get_client()` falls through to `SupabaseConfig.from_env()`, whose `sqlite_path` comes from `KASUKABE_SQLITE_PATH` and defaults to `":memory:"` (`config.py:86,110`). Every launcher sets only `TRACE_SQLITE_PATH` (`start-genesis.sh:20,37`; `run_genesis.py:3245` uses `TRACE_SQLITE_PATH` for `attach_persistence`, with its own comment at `run_genesis.py:3236-3244` explaining exactly this trap). So each daily checkpoint inserts its `world_snapshots` row into a fresh in-memory database that is closed and discarded (`run_genesis.py:1964`), and the gzipped checkpoint blob lands under `$TMPDIR/kasukabe-local-storage/world-snapshots/` (`sqlite_client.py:337-340,616-620`), not next to the trace store and not in S3. The same two-names-one-file problem exists between broker (`TRACE_SQLITE_PATH`, `config.py:473`) and the world/Litestream env templates (`KASUKABE_SQLITE_PATH`, `infra/env/*.example`). No run has crossed a day boundary yet, so this has never fired live, which is why it is unreported.
**Why it matters:** DESIGN §9's "one .db + Parquet + checkpoints" artifact loses the checkpoint index and its S3 copies; a resume after host loss has nothing to find.
**Fix:** world. Pass the runner's resolved trace config into `mirror_checkpoint_to_shared_store` (reuse `self.trace_store.client` rather than opening a second client) and make `SupabaseConfig.from_env` also honour `TRACE_SQLITE_PATH`, or pick one name and delete the other; add a wiring test that writes a checkpoint through the runner and reads `world_snapshots` back from the file at `TRACE_SQLITE_PATH`. Effort S.

### INFRA-02 - `llm_calls.tick` and `task_label` are always NULL, so per-game-day analysis and the Parquet export of calls are empty by construction
**P0** (blocks instrumentation) - `broker/trace.py:33-46,128,139`, `broker/service.py:606-633,730-755`, `sqlite_schema.sql:357-359`, `research/parquet_export.py:72-79,106-112`, `world/stakes_report.py:105-147`.
No `TraceRecord` construction passes `tick`; the ledger routes (`run_genesis.py:2878-2916`, `main.py:262-325`) return no tick; `trace.py:33-46` documents the one-line fix on each side and nobody made it. `game_day` is generated from `tick` (`sqlite_schema.sql:358`), so `export_game_day` selects `WHERE tick // 144 = ?` and gets zero `llm_calls` rows for every day (`parquet_export.py:139-141`). `task_label` was added to the schema and the dataclass (`sqlite_schema.sql:328`, `trace.py:139`, migration `20260903090000`) but is only ever computed in memory (`stakes_report.py:105-147`; its docstring says "no new write path"). The roadmap's compounding quantity 1 (tokens per task per day) therefore cannot be computed from the store, only from a report script that re-derives it each time.
**Fix:** world + broker. (a) Return `"tick": world_state.current_tick` from both `/ledger/payer` and `/ledger/debit`; read it in `HttpLedgerClient.get_payer_balance` into `PayerBalance.tick`; carry it on `PreparedCall` and into every `TraceRecord`/`BrokerEvent`. (b) Either have the runner `UPDATE llm_calls SET task_label` after each turn (join on `agent_id, tick`) or drop the column and stop claiming it. Acceptance: a mock run of two ticks yields `llm_calls` rows with `game_day = 0` and a non-empty `llm_calls.parquet`. Effort S.

### INFRA-03 - A resident at $0 is still dispatched every due tick and refused every time; the refusal storm at $0 (Build Spec D6, threshold 9.7) is not closed on the world side
**P0** (money and back-pressure) - `world/run_genesis.py:4322-4470,4911-4956`, `world/call_budget.py:118-131`, `broker/service.py:550-567`.
`dispatch_due_agents` has no dormancy check (read `run_genesis.py:4322-4470` end to end; the only `is_dormant` reads in the runner are the metrics counter at `2396` and the sweep at `4925`); `world/core/scheduler.py`, `engine.py` and `perception.py` contain no `dormant` handling (grep empty). `budget_for` explicitly gives a resident at zero the floor of two calls (`call_budget.py:118-124`). So a dormant resident is woken every third tick, its engine calls the broker, the broker refuses 402 pre-dispatch, writes a trace row and an event, the engine's `/tick` returns 500, the runner logs a failed dispatch, and this repeats until `check_dormancy_death` (24 game-hours = 144 ticks, ~48 refusals per broke resident), plus the engine's own awake-interval learning batches (`sim/config.py:142`) which are refused the same way. The barren guard only trips when **every** dispatch in a tick fails for 20 ticks (`run_genesis.py:4487-4494,5191`), so a town with one broke resident and nineteen solvent ones never trips it. The 3,651 `InsufficientFundsError` rows in `dryrun.db` (Build Spec D6) are this path; the wallets in `genesis_20.json` are $6 each and run 23 ended at $5.25-5.85, so it has simply not been reached since.
**Fix:** world (seam untouched). In `dispatch_due_agents`, skip residents whose `dormancy.is_dormant` is true (they are by definition not thinking) and skip the engine-side learning gate for them by sending `is_sleeping=True` or a new `dormant` flag on `/tick` (the engine's `/tick` already takes `is_sleeping` and makes no interactive call for it, `agent_adapter.py:117-128`). Acceptance test: seed one agent at 0 cents, run 10 ticks against the mock broker, assert zero `llm_calls` rows for that agent and one `agent.dormant` event. Effort S.

### INFRA-04 - The systemd/env deployment path in `app/infra/` cannot boot the current broker or a genesis run
**P0 for a metal deployment, P1 otherwise** - `infra/env/broker.env.example:6,15,24-49`, `broker/config.py:431-439,473`, `infra/systemd/kasukabe-broker.service:26-38`, `infra/systemd/kasukabe-world.service:23`, `infra/RUNBOOK.md` "Known gaps" §1.
(a) `broker.env.example` sets `MOCK_MODE=false`; `BrokerSettings.build_from_env` now rejects any `MOCK_MODE` value with a `ConfigError` (`config.py:435-439`), so `kasukabe-broker.service` crash-loops at boot. (b) The template sets `KASUKABE_SQLITE_PATH=/opt/kasukabe/data/kasukabe.db` and never `TRACE_SQLITE_PATH`; the broker reads only the latter (`config.py:473`) and defaults to `/data/kasukabe.db` (`config.py:285`), which under the unit's `ProtectSystem=strict` + `ReadWritePaths=/opt/kasukabe/data` is unwritable; and Litestream would tail a different file from the one the broker writes. (c) `RPM_PER_MODEL=100` is not a setting the broker reads (`RPM_PER_LANE` is, `config.py:479`). (d) `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` and the whole "BLOCKER" paragraph describe a `SupabaseRestSink` wiring that was removed; `RUNBOOK.md` "Known gaps" §1 repeats it. (e) `kasukabe-world.service` runs `world.app:app`, the health-only scaffold (HANDOVER §7.2.6), not `world.main:app`, and there is no unit for `world.run_genesis` at all, so the services this runbook starts cannot run a town. (f) `infra/kimi-quota-request.md` still describes a single-model 100 RPM design.
**Fix:** infra. Rewrite `broker.env.example` (drop `MOCK_MODE`, `SUPABASE_*`, `RPM_PER_MODEL`; add `TRACE_SQLITE_PATH` equal to `KASUKABE_SQLITE_PATH`; add `BEDROCK_INFERENCE_PROFILES`), add a `kasukabe-genesis.service` that runs `python -m world.run_genesis --live --world-mode embedded` with `EnvironmentFile`, point the world unit at `world.main:app` or delete it, and add a test that parses every `*.env.example` through `BrokerSettings.build_from_env`/`validate` so a template that cannot boot fails CI. Effort M.

### INFRA-05 - Shared secrets are literals in a committed launcher, and the default auth mode lets any client spend any wallet
**P1** (security; money) - `app/start-genesis.sh:24,35-36`, `broker/auth.py:75-79`, `broker/config.py:276`, `infra/systemd/kasukabe-broker.service:26`, `world/run_genesis.py:294`.
`AUTH_SHARED_SECRET=kasukabe-agent-secret` and `OPERATOR_TOKEN=kasukabe-genesis-2026` are hard-coded in `start-genesis.sh` and therefore in git history; `DEFAULT_BROKER_API_KEY = "kasukabe-sim-broker"` (`run_genesis.py:294`). With the secret empty (the `BrokerSettings` default, `config.py:276`), `resolve_agent_id` trusts the bare token as the agent id (`auth.py:75-79`), so any process that can reach the broker can bill any resident's wallet by naming it; the systemd unit binds `0.0.0.0:8200` for the microVM bridge. `broker/app.py:211-222` also echoes raw exception text to clients.
**Fix:** infra + broker. Read both secrets from an env file or `openssl rand` at launch (`start-genesis.sh` should refuse to start with the literal values, mirroring `05-deploy-services.sh:435-438`'s `REPLACE_ME` guard); make `auth_shared_secret` required when `mock_mode` is False (`config.py:384-394`); mint tokens with `infra/scripts/07-mint-agent-tokens.py`. Effort S.

### INFRA-06 - Reasoning is carried but never requested (Build Spec D2, second half; F1 still open)
**P1** (instrumentation) - `broker/translate.py:337-366,414-416`, `broker/service.py:511-518`, `engine/src/herald/provider.py:810-829`, `docs/BUILD_SPEC_V2.md:676-709`.
The broker translates `thinking` -> `reasoning_config` and carries `reasoningContent` back on both paths (tested by `test_stream_fidelity.py` and `test_reasoning_request.py`). But `MessagesRequest.thinking` only arrives if herald sets it, and herald sets it only for `super_thinking` or a latched session (`provider.py:824-829`); the sim never resolves such a tier (BUILD_SPEC F1). Run 23's session logs show Kimi emitting `</think>` **inside text** (`docs/runs/run-23-report.md:882`), i.e. the model reasons in-band and the record cannot separate reasoning from speech. Threshold 9.5 (reasoning captured 100%) is unmet and cannot be met from `app/` without one of F1's two options.
**Fix:** decide F1. Cheapest legal option: the seam wraps the adapter returned by `guarded_create_llm` (`model_profile.py:654-670`) so `stream_with_tools`' kwargs gain `thinking={"type":"enabled","budget_tokens":N}` (a `sim/*` change), then one live call proves whether Kimi on Bedrock honours `reasoning_config`. Alternatively, have the broker inject `reasoning_config` for `auto:agent_turn` calls under an operator knob (`KASUKABE_REQUEST_REASONING`), which is a broker-only change but couples routing policy to research settings. Effort S either way; the live probe is the real cost.

### INFRA-07 - Kimi's text-form tool calls are parsed by nothing; the broker is the right place to parse them
**P1** (turns lost, money paid) - `broker/translate.py:700-757`, `broker/service.py:182-291`, `docs/review/01-run-23-evaluation.md:64-70`.
Rosalind Vance's nine sessions in run 23 emitted `<|tool_call_argument_begin|>`-style markup as plain text; herald only acts on `tool_use` blocks, so the calls were never dispatched and the turns were billed for nothing. The engine cannot fix this (parsing lives in frozen `herald/provider.py`). The broker already rewrites the stream (synthetic starts, `translate.py:724-732`) and reassembles it (`StreamAccumulator`), so it can recognise the Kimi markers in `text` deltas, buffer until the matching end marker, and emit a real `content_block_start{tool_use}` + `input_json_delta` + `content_block_stop` sequence with a synthetic `toolu_` id, plus a `stop_reason: tool_use` `message_delta`; the trace keeps both the raw text and the derived block so the repair is visible. A second, seam-side option is a wrapper around the herald adapter in `engine/src/sim/` that post-processes `StreamEvent`s, but it would have to reimplement the same buffering after herald has already classified the deltas as text.
**Fix:** broker. New `broker/kimi_toolcalls.py` with a streaming state machine, wired in `anthropic_sse_events_from_converse_stream` behind a per-model flag; counted in `delta_counts()` as `tool_use_from_text`; adversarial tests with markers split across deltas. Effort M.

### INFRA-08 - Raw LLM payloads are not archived; the trace body cap fires on most calls; "complete causal record" is not true
**P1** - `broker/trace.py:476-509,606-618`, `supabase/archiver.py:88-97`, DESIGN §9.
`capped_body` truncates at 64 KB (median request 76 KB, largest 154 KB per its own comment, `trace.py:489-493`), keeping the last 6 messages and 8,000 chars of any oversized string; the system prompt (persona + law, ~150 KB measured) is cut. `PayloadArchiver.archive_payload` exists for exactly this (gzip to S3 with sha256) and has no caller in `broker/`. So the prompt a resident actually thought against is recoverable only partially, and never for the system prompt in full.
**Fix:** broker. In `settle`, after building the `TraceRecord`, hand `(request_body, response_body)` to a `PayloadArchiver` on a second `BatchWriter` (batched, async, never blocking) with key `build_llm_call_archive_key(agent_id, tick, trace_id)`, and add `payload_archive_path` + `payload_sha256` columns to `TraceRecord`/`llm_calls` via `schema_gen`. Keep the 64 KB cap for the inline copy. Effort M.

### INFRA-09 - No run manifest: code hash, engine hash, model ARNs/versions, prices, seeds and knobs are not recorded with the trace; engine minds are not checkpointed
**P1** (reproducibility) - `world/run_genesis.py:500,2986,3106-3126`, `world/core/checkpoint.py:46-53`, `broker/config.py:88-99`, `world/run_report.py:760-775`.
What is recorded: `population_seed` (default 2026) in `RunnerCounters.rng_state` inside checkpoints; per-row `model`/`requested_model`/`region` in `llm_calls`; a `run_id` read from `/world/state` by the report. What is not: the git commit of `app/` and `engine/` (grep for `git`, `commit`, `code_hash`, `__version__` in `world/*.py` and `engine/src/sim/*.py` is empty), the inference-profile ARN or Bedrock model version actually invoked (`bedrock.py:132-142` swaps `modelId` and the trace keeps the logical id), the price table in force (`config.py:96-99`), `SPEND_CAP_USD`, `KASUKABE_CONTEXT_WINDOW`, `KASUKABE_LOCAL_TOOL_SCHEMAS`, `KASUKABE_LEARN_AWAKE_TICKS`, `KASUKABE_TRACE_BODY_LIMIT`, `KASUKABE_TICK_WALLET_FRACTION_BPS`, the ablation-arm assignment, and the genesis file's hash. The daily checkpoint holds `ledger, world_state, world_locations, runner_counters, charter, records` (`run_genesis.py:3106-3126`) but not the 20-250 `brain.sqlite`/`session_log.jsonl`/`memory_stream.jsonl` under `/tmp/kasukabe-agents/<id>` (`SYSTEM_REVIEW.md:11-29`), so a "restore to any game-day" (ROADMAP §6) restores a town of amnesiacs. Temperature/top_p are not set by herald, so Bedrock defaults apply and are unrecorded.
**Fix:** world + broker. Write `runs/<run_id>/manifest.json` at boot (commits via `git rev-parse` of both trees or a tree hash, genesis sha256, seed, every `KASUKABE_*`/`SPEND_*` knob, `MODEL_PRICES_USD_PER_MILLION`, `BEDROCK_INFERENCE_PROFILES`, the broker's `/health` and `/admin/lanes` snapshot); have the broker record `inference_profile_id` on `TraceRecord`; at each daily checkpoint tar the agent home dirs alongside `checkpoint_<tick>.json.gz`. Effort M.

### INFRA-10 - The Parquet export and the S3 replication have no production caller; the DuckDB extension is fetched at first use
**P1** - `research/parquet_export.py:155-188`, `app/Dockerfile:52-64`, `infra/litestream.yml`, `start-genesis.sh`.
`export_game_day` is never scheduled (no call in `world/`, no cron, no systemd timer). `INSTALL sqlite` runs on every export (`parquet_export.py:126`), which downloads on a fresh host (the Dockerfile pre-fetches it for the image only) and failed here with HTTP 403. Litestream is documented but no launcher starts it and `KASUKABE_S3_BUCKET` is never set, so `SqliteClient.upload_archive` always takes the local-directory branch (`sqlite_client.py:598-614`).
**Fix:** world/research. Call `export_game_day` from the runner's checkpoint sink (same day boundary), read-only; make the extension install `INSTALL sqlite FROM ...` with a vendored path or pre-installed check; add `litestream` to `start-genesis.sh`/compose as a sidecar with the same `TRACE_SQLITE_PATH`. Acceptance: a two-day mock run leaves `exports/parquet/game_day=0/llm_calls.parquet` with rows (requires INFRA-02). Effort S.

### INFRA-11 - `broker.app` opens the production trace file at import time; the test suite cannot be collected on a fresh checkout and the docs still say mock is the default
**P1** (test hygiene) - `broker/app.py:1-31,335`, `broker/config.py:259`, `broker/README.md:12-13,64,94`, `broker/tests/test_broker_app.py:12,42-51`.
`app = create_app()` at import builds a real `SqliteClient(SupabaseConfig(sqlite_path="/data/kasukabe.db"))`. On any machine without `/data` (this one), importing `broker.app` raises and pytest aborts collection under `-x`. `test_default_app_boots_against_real_bedrock` asserts the real-mode default while depending on a writable `/data`. `app.py`'s docstring (lines 9-16) and `README.md` describe a mock-by-default broker that no longer exists; `docker-compose.yml:126-140` still documents `MOCK_LEDGER_BALANCE_CENTS` as a mock-mode knob.
**Fix:** broker. Build the app lazily (`app = create_app()` behind a `uvicorn --factory` or an `ASGI` factory) or make `build_service` open the SQLite file on lifespan startup, not import; update the docstring/README; make `test_broker_app.py` construct its own app with `trace_sqlite_path=":memory:"`. Effort S.

### INFRA-12 - Throughput pacing assumes one call per due resident; the call budget allows 12 plus housekeeping; the 250-resident math does not close
**P1** - `world/run_genesis.py:4087-4141`, `world/call_budget.py:43-48,70`, `broker/lanes.py:412-441`, `broker/config.py:398-406`.
`resolve_tick_interval` sizes the tick at `agents * 0.3 * 60 / capacity` requests per minute, i.e. one call per due resident. A due resident may make up to 12 model calls (`MAX_CALLS_PER_TICK`) plus the post-turn summariser (`purpose.py:56-77`: roughly one per turn) plus periodic learning batches of 55-108K tokens. Run 23: 1,573 calls over 47 awake ticks for 20 residents is ~1.7 calls per resident-tick on average, with a measured worst case of 50 in earlier runs. Capacity is what the account's inference profiles allow: `config.py:398-400` records that **this deployment has both models in ap-south-1 only**, i.e. 2 lanes = 200 RPM, not 600; `build_lane_pool` warns but proceeds (`lanes.py:430-441`). At 250 residents: 75 due per tick x (1.7 mean .. 13 worst) = 128-975 calls per tick against 200-600 RPM, i.e. 13-290 s per tick just for the buckets; the 120 s `tick_timeout_s` then discards paid turns (10 in run 23 at 20 residents). Cost: run 23 spent $9.70 for 20 residents over one morning (47 awake ticks); scaled linearly, 250 residents cost ~$120 per morning and ~$250 per game-day, against ROADMAP rung 3's $15/day. The wallets in `genesis_20.json` ($6) already exceed DESIGN's $2, and the treasury is $880.
**Fix:** world. Pace on the runner's own observed call rate (count `steps_used` from `/tick` responses and the broker's `/admin/lanes` `tokens_available`) rather than a fixed fraction; make `tick_timeout_s` a function of `call_budget x p95 latency`; make the engine abort a turn when the runner disconnects (engine finding) so a discarded turn stops spending. Effort M.

### INFRA-13 - The pre-dispatch estimate excludes tool schemas and tool inputs, and an un-collected debit is only remembered in process memory
**P2** (money) - `broker/service.py:122-157,678-696`, `broker/pricing.py:145-159`.
`request_text_chars` deliberately ignores tool schemas (76 tools ~ 8,000 tokens per its own measurement at `STAKES_TIME_COMPOUNDING.md:225-235`) and `tool_use`/`tool_result` JSON; the `max_tokens` ceiling covers most of the gap, but a call with 8,000-40,000 unbilled input tokens is 0.3-1.4 cents under-estimated, which is exactly the range in which a wallet near zero passes the check and then fails the debit. The failed debit is credited back into `CostAccountant.remainders_nano` (`service.py:695`), which lives only in the broker process; a broker restart forgets it, and the `burned` pool then understates real spend by that amount forever (the $1000 invariant still holds, the dollars simply never left a wallet). There is no `broker.ledger.debit_failed` event; the signature is a trace row with `cents_debited == 0 and cost_nano_dollars > 0` (`service.py:683-687`).
**Fix:** broker. Include `len(json.dumps(tools))` and tool-block JSON in the estimate (the same serialisation `fit.py:86` already does), emit an event on debit failure, and persist `remainders_nano` to a small `broker_state` table (or replay it from `llm_calls` at boot: sum of `cost_nano_dollars` minus `cents_debited` per payer). Effort S.

### INFRA-14 - The broker's spend governor and lane/circuit state reset on restart
**P2** - `broker/spend.py:79-84`, `broker/app.py:194-206`.
`spent_nano_dollars` starts at 0 on every boot, so a broker restart mid-run lifts the cap. The runner's ledger-based governor (`run_genesis.py:2077-2097`) is checkpointed and covers the same line, but only for calls billed through this ledger; and `SpendState.HALTED` is not restored, so `/admin/spend` reads `normal` after a restart. Same for circuit-breaker trips.
**Fix:** broker. Seed the governor at boot from `SELECT SUM(cost_nano_dollars) FROM llm_calls` on the configured store (one query, the store is already open) and expose `since_boot` separately. Effort S.

### INFRA-15 - Bedrock streaming pulls every event through `asyncio.to_thread`, so the default thread pool becomes the concurrency ceiling at 250 residents
**P2** (throughput) - `broker/bedrock.py:158-179`.
`converse_stream` calls `asyncio.to_thread(next, stream, sentinel)` per event. Python's default executor is `min(32, cpu+4)` workers; 250 concurrent streams each pulling hundreds of deltas will queue on it, adding latency that is invisible in `rate_limit_wait_ms` and shows up as tick timeouts. Fine at 20 residents.
**Fix:** broker. One dedicated thread per in-flight stream feeding an `asyncio.Queue` (one `to_thread` per call, not per event), or a sized `ThreadPoolExecutor` passed to `loop.run_in_executor`. Effort S.

### INFRA-16 - World events are not recorded in a genesis run; `events` holds only `broker.*` rows
**P1** (instrumentation) - `world/run_genesis.py:3217-3224`, `world/wiring.py:741-747,1052-1071`.
`attach_persistence` deliberately skips `record_event` because an earlier run's mirror of every perception grew the store to 21 GB. The consequence is that the "complete causal record" (DESIGN §2) has no `agent.moved`, `economy.wallet.debited`, `agent.dormant`, `politics.*`, `health.*` rows; the report reads them from in-memory `records` and checkpoints only.
**Fix:** world. Record events selectively by domain (everything except perception mirrors and `world.tick.advanced`), with payloads capped like `capped_body`, through the existing `record_event`; add the domain allowlist to the manifest. Effort M.

### INFRA-17 - Six trace tables have no writer: `relationships`, `organizations`, `petitions`, `cases`, `buildings`, `eval_metrics`
**P1** (instrumentation; blocks the organisations/government/expansion targets) - `sqlite_schema.sql:134-266,551-578,606-633`, `wiring.py:552-558`.
The charter store keeps organisations, petitions and cases in memory and in checkpoints (`world/charter/store.py:79`), the engine keeps the social graph in each `brain.sqlite`, and nothing writes any of the six tables. Build Spec §4.3 requires a parametrised test that fails on any table with no live writer or stated exclusion; no such test exists.
**Fix:** world. Add recorders in `TraceStore` for `politics.*` (petitions, amendments, elections -> `petitions`/`organizations`), `crime.*` (`cases`), `build_structure` (`buildings`), and a nightly export of each engine's social graph into `relationships`; add the §4.3 table-coverage test with an explicit exclusion list. Effort M.

### INFRA-18 - Trace-writer halt is invisible to the runner except as barren ticks
**P2** - `broker/app.py:138-163,281-283`, `world/run_genesis.py:4065-4084`.
When both `BatchWriter`s halt, every `/v1/messages` returns 503; herald retries 5xx three times (`transport.py:47-52`) then the turn fails; the runner only learns after 20 all-fail ticks. `broker_terminal_reason` polls `/admin/spend` but not `/admin/trace`.
**Fix:** world. Poll `/admin/trace.dispatch_halted` in `broker_terminal_reason` and stop with the writer's `last_failure_message`. Effort S.

### INFRA-19 - The learning-centre and summariser calls are classified and routed, but billed to the resident and run mid-morning at 55-108K tokens
**P2 / design question** - `broker/purpose.py:47-77`, `engine/src/sim/config.py:122-142`, run 23 report (43 `auto:learning_centre.knowledge_extraction`, 8 `episode_creation`, 8 `social_graph`, 6 `summarizer.post_turn` rows visible in the recent-calls table; one at 107,289 input tokens for $0.0405).
Classification works (RC-5 is closed at the broker), and background goes to DeepSeek lanes when a DeepSeek profile exists. But DESIGN §4 says the learning centre is "metered on the agent's own wallet, scheduled during sleep ticks", and the awake-interval batch (every 18 ticks) now runs it mid-morning at up to 4 cents a call, which at $6 wallets is 0.7% of a life per batch, and Build Spec 3.7 asks whether housekeeping should drain wallets at all. Also, `MODEL_ID` is pinned to Kimi in the seam, so `requested_model=kimi, model=deepseek` on every background row is the expected signature and should be documented for readers of the trace.
**Fix:** operator decision, then world/broker: either keep wallet billing and lower the interval, or bill background to a `learning` account (a new `LedgerClient.debit(..., purpose)` branch on the world side). Effort S once decided.

### INFRA-20 - The Firecracker track is unwired and untested on hardware; the process supervisor is the only real backend
**P2** (documented) - `world/run_genesis.py:5455-5458`, `infra/firecracker/README.md:20-35`, `infra/firecracker/tests/test_real_firecracker_integration.py`.
`--supervisor-backend firecracker` is refused by the runner; the kernel was never compiled; the real-hardware test is skipped everywhere; `S3SnapshotStore` shells out to `aws` CLI; the rootfs bakes the engine but the runner's `extra_env` contract (`AGENT_ID`, `WORLD_BASE_URL`, `KASUKABE_HOME_ROOT`, `SIM_AGENT_PORT`, `KASUKABE_ABLATION_ARM`) must ride the kernel command line (`vm_manager.py:build_boot_args`), which has never been tried. A 250-resident deployment today is 250 OS processes on one host (memory measured 27-88 MB each, `SYSTEM_REVIEW.md:27-29`, `kasukabewhatismissing.md:87`), which fits an m5.metal easily.
**Fix:** operator decision (see Open questions). If kept: a `kasukabe-genesis.service` + `FIRECRACKER_REAL_HW=1` run on the metal host before any claim. Effort L.

### INFRA-21 - IAM policy model ARNs and the inference-profile wildcard need verification
**P2** - `infra/scripts/01-setup-s3-and-iam.sh:81-85`.
The policy allows `foundation-model/moonshotai.kimi-k2-5*` and `foundation-model/deepseek.v3-2*` (dashes) while the allowlist ids are `moonshotai.kimi-k2.5` / `deepseek.v3.2` (dots) and `discover_profiles.py:63-70` matches ARNs by substring of the dotted id; if Bedrock's foundation-model ARNs use the dotted form the policy denies the bare-model path and only the `inference-profile/*` wildcard works (which the script's own note flags). Inference is only what the comment says; needs `aws bedrock list-foundation-models` on the account.
**Fix:** infra. Verify and pin exact ARNs; make the script print them. Effort S.

### INFRA-22 - The Postgres migrations are dormant but still extended, and preflight points at them for a SQLite store
**P3** - `supabase/migrations/20260825120000_llm_calls_failed_at.sql`, `20260902090000`, `20260903090000`; `world/preflight.py:488-495`; `supabase/tests/test_supabase_migrations_apply_live.py:18-27`.
Three new migrations were written for a database that nothing applies; the live-migration test skips wherever `psql` is absent (all CI). Preflight check 11's remedy says "apply supabase/migrations/20260825120000_llm_calls_failed_at.sql" for a SQLite file that `ensure_schema` already migrates additively (`sqlite_client.py:350-386`).
**Fix:** supabase. Freeze or delete `migrations/` (README already calls it dormant) and fix the preflight remedy text to "restart the writer; `SqliteClient.ensure_schema` adds the column". Effort S.

### INFRA-23 - `MockBedrockGateway` streams a `contentBlockStart` for text that real Bedrock omits
**P3** - `broker/bedrock.py:257-266`, `broker/tests/test_stream_fidelity.py:10-17`.
The mock is friendlier than production, which is how D1 hid for fifteen runs. `test_stream_fidelity.py` covers the bare-delta shape directly, but every end-to-end test through `build_service` still exercises the friendly shape.
**Fix:** broker. Default the mock to omit `contentBlockStart` for text (a `bedrock_style="bare_text"` flag defaulting on). Effort S.

### INFRA-24 - Orphaned code with tests that cannot fail meaningfully
**P3** - `broker/rate_limit.py:112-141` (`RateLimiterRegistry`), `broker/trace.py:192-232` (`SupabaseRestSink`), `broker/tests/test_rate_limit.py:136-160`, `broker/tests/test_trace.py:149-170,315-337`, `broker/tests/test_trace.py:272-312` (asserts dataclass defaults).
**Fix:** delete the two classes and their tests; replace the tick-default tests with the wiring test from INFRA-02. Effort S.

### INFRA-25 - `HttpLedgerClient`'s contract is pinned only against its own mock transport
**P2** - `broker/tests/test_ledger.py:138-222`, `world/run_genesis.py:2878-2916`, `world/main.py:262-325`.
Both world hosts implement the shape, but no test starts the embedded world app and drives `HttpLedgerClient` against it (`tests/test_console_world_integration.py` covers the console only). A field rename on either side would pass every suite. Also the embedded route returns 409 on a halted ledger (`run_genesis.py:2910-2911`), which `HttpLedgerClient` maps to `LedgerUnavailableError` 502 (`ledger.py:286-304`) rather than a distinct "ledger halted" refusal.
**Fix:** `app/tests/`. One cross-package test: build the embedded app via `build_embedded_world_app`, mount it in an `httpx.ASGITransport`, and run `BrokerService` with `HttpLedgerClient` against it through a mock Bedrock; assert payer/debit/402/409 shapes. Effort S.

### INFRA-26 - `events.id` is a UUID text PK for both writers but `SqliteClient.fill_generated_id` decides by column type at runtime per table; `utterances`/`memories` use autoincrement; fine, but `events` from the world carry no `model` and the broker's carry no `tick`
**P3** - `supabase/schema_gen.py:343-378`, `wiring.py:741-747`.
Cosmetic until INFRA-02 lands: after it, both writers should carry `tick`.

### INFRA-27 - Reconcile alarm has no consumer
**P3** - `broker/service.py:361,705-728`.
`content_loss` is an unbounded in-process list and the `broker.content.lost` event goes to `events`; nothing (runner, preflight, observatory) reads either, so threshold 9.4 is enforced by grep after the fact.
**Fix:** expose `len(content_loss)` on `/admin/trace` and have preflight/`observatory` fail on it. Effort S.

### INFRA-28 - `docker-compose.yml` references services and knobs that do not exist
**P3** - `docker-compose.yml:126-140,161`.
`observatory` points at `http://genesis:8100` with no `genesis` service; `broker` documents `MOCK_LEDGER_BALANCE_CENTS` as a mock-mode knob the broker can no longer enter. Effort S.

### INFRA-29 - The 120 s tick timeout discards paid turns and the engine keeps running them
**P1** (money) - `world/run_genesis.py:566-573,4470-4476`, `engine/src/sim/pinned_boot.py:230-260`.
When the runner's httpx timeout fires, the engine's `/tick` handler keeps executing the turn (uvicorn does not cancel on client disconnect), the broker keeps billing every remaining call, and the next `/tick` for that resident serialises on `_turn_lock`, so a slow resident cascades into consecutive timeouts. Ten turns were discarded this way in run 23 after paying.
**Fix:** world + engine-finding. Runner: size the timeout from `call_budget` and measured per-call latency, and never re-dispatch a resident whose previous `/tick` is still in flight (track outstanding requests). Engine finding: `agent_adapter.py`'s `/tick` should observe `request.is_disconnected()` between agent-loop steps and cancel the coordinator turn; that is a `sim/*` file, so it is legal under Law 1. Effort M.

### INFRA-30 - Test-suite portability
**P2** - see the test run at the top. Collection depends on `/data`; 9 research tests depend on network at first use; 2 tests depend on `smallville/` being checked out. None of these is a code defect in the module under test, but together they mean the "534 passed" line requires three undocumented preconditions.

---

## 4. Gaps vs DESIGN.md

| DESIGN promise | State | Where |
|---|---|---|
| §4/§9 six lanes, 100 RPM each, 600 aggregate | Built; the account has profiles in ap-south-1 only, so 2 lanes / 200 RPM in practice | `config.py:398-406`, `lanes.py:412-441` |
| §4 "trace must record which lane and model served every call" | Done (`model`, `region`, `requested_model`) | `trace.py:83-139` |
| §4 background work on DeepSeek lanes, tagged | Done by inference at the broker (RC-5 closed there); header still sent by nothing | `purpose.py` |
| §4 spend governor: warn 80%, stop and checkpoint at 100% | Warn/halt done; nobody checkpoints on `system.sim.halted`; runner stops on its own governor/poll | `service.py:416-426`, `run_genesis.py:5175-5201` |
| §4 parent-debit for minors | Done server-side | `core.py:453-463` |
| §3 dormancy at $0, revival, death | Dormancy swept per tick in the runner; still dispatched (INFRA-03); never observed live | `run_genesis.py:4911` |
| §9 broker "batches trace writes async, never blocks inference" | Done | `trace.py:349-473` |
| §9 SQLite WAL trace store, tables listed | 16 tables exist; 6 never written, `events` broker-only in genesis | INFRA-16/17 |
| §9 Litestream WAL -> S3 | Config only; never started by any launcher | INFRA-10 |
| §9 raw LLM payloads gzipped to S3 | Not built (archiver has no broker caller) | INFRA-08 |
| §9 per-game-day Parquet export to S3 | Built, uncalled; `llm_calls` would export empty | INFRA-02/10 |
| §9 checkpoint full world state every game-day | Local file yes; shared/S3 mirror lands in `:memory:` (INFRA-01); engine minds excluded (INFRA-09) | |
| §9 250 Firecracker microVMs | Not wired; processes only | INFRA-20 |
| §9 "Portable artifact: one .db + Parquet" | Missing tick/task_label on calls, missing manifest, missing events/relationships | INFRA-02/09/16/17 |
| §2 "the trace is a complete causal record" | Not true today (above) | |
| §11 costs "$0.00343 per call" | Run 23: $0.0062 per call, 15-20K input tokens per turn | run-23 report |

---

## 5. Gaps vs the target

**Thriving, not surviving.** The money pipe meters correctly and cheaply enough per call, but the town's cost of living is set by the pacing and turn shape, not by the broker: 1.7 mean calls per resident-tick at ~16K input tokens is $0.25/resident/game-day, 2.5x the $6 wallet per day at 20 residents. What exists: call budget, location-relevant schemas, compaction at 10K, `keep_doing`. What to add: the runner-side pacing and timeout work (INFRA-12/29) so paid turns are not discarded, and a per-resident daily USD budget line in the manifest so "mastery lowers the cost of living" can be measured as dollars per task per day (needs INFRA-02).

**Organisations and government.** The trace has `organizations`, `petitions`, `cases`, `buildings` tables and no writers (INFRA-17). Everything institutional currently lives in the charter store and checkpoints; a researcher cannot query "which petitions were filed on day 3" from the .db. Add `TraceStore` recorders for `politics.*` and `crime.*` events and a buildings recorder on `build_structure` completion.

**Expansion and construction.** `buildings` never written; `world_snapshots` (the only place map growth would be visible) is lost (INFRA-01). Fix INFRA-01/17 first; then record `world.land.expanded` events.

**Skills/tools that cut model calls.** Measurable only with `tick` and `task_label` on `llm_calls` (INFRA-02) and with reasoning separated from speech (INFRA-06). Add both, then C1 becomes a SQL query rather than a report script.

**Supply-demand and ideology-driven mechanics.** `transactions` now carries `counterparty_id`; `post_price` events are in memory only. Record `economy.price.posted` into `events` (INFRA-16) so price series are queryable per day.

**Evolving goals.** `memories` is written; the engine's evolved soul and social graph are only in each `brain.sqlite`, which no checkpoint or export touches (INFRA-09). Add a nightly export of `souls` (evolved-soul text per agent per day) and `relationships` from the agent homes into the trace store, from the runner (world), reading files the seam already writes.

**World-class instrumentation.** Needs, in order: INFRA-01, INFRA-02, INFRA-09 (manifest + engine-home archive), INFRA-08 (raw payloads), INFRA-16/17 (events and the six tables), INFRA-06/07 (reasoning and Kimi tool-call fidelity), INFRA-10 (scheduled Parquet + Litestream), and one Build Spec §4.3-style test asserting every table has a live writer.

---

## 6. Recommended work items (P0 first)

1. **INFRA-01 Fix the checkpoint mirror's database.** Reuse the runner's `trace_store.client` in `mirror_checkpoint_to_shared_store`; unify `TRACE_SQLITE_PATH`/`KASUKABE_SQLITE_PATH`. Acceptance: `world/tests` boots a mock run for one game-day with `TRACE_SQLITE_PATH=tmp.db`; `SELECT count(*) FROM world_snapshots` on that file is 1 and `state_archive_path` resolves to a file under the trace directory.
2. **INFRA-02 Put `tick` on every broker row.** Both ledger routes return `tick`; `PayerBalance.tick`; `TraceRecord.tick` and `BrokerEvent.tick` set; decide `task_label` (write-back or drop). Acceptance: after two mock ticks, every `llm_calls` row has `tick` and `game_day` non-null and `export_game_day(0)` produces a non-empty `llm_calls.parquet`.
3. **INFRA-03 Stop dispatching the dormant.** Skip `dormancy.is_dormant` residents in `dispatch_due_agents` and suppress their engine learning batches. Acceptance: one agent seeded at 0 cents, 10 ticks, zero `llm_calls` rows for it, one `agent.dormant` event; Build Spec 4.5 bound asserted (<= 1 refusal per broke resident).
4. **INFRA-04 Make the infra path boot.** Env templates parsed by `BrokerSettings` in a test; a `kasukabe-genesis.service`; RUNBOOK "Known gaps" rewritten. Acceptance: `python -c 'BrokerSettings.build_from_env(read_env_file("infra/env/broker.env.example"))'` returns no errors; `systemd-analyze verify` passes.
5. **INFRA-05 Secrets out of git.** `start-genesis.sh` refuses the literal secrets; broker requires `AUTH_SHARED_SECRET` in real mode. Acceptance: `BrokerSettings(mock_mode=False, auth_shared_secret="")` fails validation; grep for `kasukabe-agent-secret` in the tree is empty.
6. **INFRA-29/12 Pacing and timeouts.** Outstanding-request tracking per resident; timeout derived from `call_budget`; engine `/tick` cancels on disconnect (seam). Acceptance: a mock engine that sleeps 200 s is not re-dispatched while in flight, and `timed_out_ticks` no longer coincides with paid calls in the trace.
7. **INFRA-07 Parse Kimi text-form tool calls at the broker.** Acceptance: a synthetic Bedrock stream carrying `<|tool_call_begin|>...<|tool_call_end|>` split across three deltas yields one `tool_use` block with parsed input, `stop_reason=tool_use`, and `delta_counts()["tool_use_from_text"] == 1`; herald's parser (run in-process in a test) dispatches it.
8. **INFRA-06 Decide F1 and probe reasoning.** One live call with `reasoning_config` on Kimi and DeepSeek; record the answer in a run report; then wire the seam or the broker knob. Acceptance: `session_log.reasoning` non-empty on a real turn, or a documented "unsupported" result.
9. **INFRA-08 Archive raw payloads.** Second `BatchWriter` to `PayloadArchiver`; `payload_archive_path`, `payload_sha256` on `llm_calls`. Acceptance: for every successful row, the archive decompresses to a body whose sha256 matches.
10. **INFRA-09 Run manifest and engine-home archive.** Acceptance: `runs/<run_id>/manifest.json` exists at boot with app/engine commit, genesis sha256, seed, knobs, prices, profiles; daily checkpoint tarball contains every `brain.sqlite`.
11. **INFRA-16/17 Record events and the six empty tables; add the table-coverage test.** Acceptance: a parametrised test over `sqlite_schema.sql` tables fails for any table without a live writer or an explicit exclusion string.
12. **INFRA-10 Schedule Parquet and start Litestream.** Acceptance: mock two-day run leaves `exports/parquet/game_day=1/` and a `litestream` replica directory (local file replica in tests).
13. **INFRA-11/24/22/23/28 Hygiene.** Lazy app construction; delete `SupabaseRestSink`/`RateLimiterRegistry`; freeze `migrations/`; stricter mock stream; compose cleanup.
14. **INFRA-13/14/18/27 Broker robustness.** Estimate includes tools; remainder persisted; governor seeded from the store; runner polls `/admin/trace`; `content_loss` on `/admin/trace`.

---

## 7. Tests: what exists, what cannot fail, what is missing

**Exists (346 broker, 137 supabase, 32 research, 10 app-level).** Strong: `test_stream_fidelity.py` (D1 adversarial event orders, billed-vs-recorded reconciliation, wired-to-settle check), `test_a_doomed_request_is_refused_here.py` (fit + purpose, wired into `prepare_call`), `test_an_orphan_tool_result_does_not_kill_the_turn.py`, `test_a_capped_body_is_still_readable.py`, `test_trace_sink_notices_a_vanished_store.py` (inode check through `/admin/trace`), `test_service.py` (parent debit, spend cap ordering, ledger-debit-failure credit-back, mid-stream failure settling), `test_lanes.py` (breaker state machine, overflow), `test_inference_profiles.py`, `supabase/tests/test_schema_gen.py` (dataclass <-> DDL diff on real SQLite), `test_schema_reaches_an_existing_database.py`, `test_supabase_crash_safety.py` (real SIGKILL), `test_supabase_sqlite_client.py::test_concurrent_inserts_from_two_tables_never_lose_a_row`, `test_supabase_s3_client.py` (SigV4 against a botocore reference), `test_research_parquet_export.py` (real DuckDB, when the extension is present).

**Cannot fail, or test orphaned code:** `test_trace.py:272-312` (asserts a dataclass default is `None`); `test_trace.py:149-170,315-337` (`SupabaseRestSink`, no production caller); `test_rate_limit.py:136-160` (`RateLimiterRegistry`, no production caller); `test_reasoning_request.py` (a request path with no caller: green forever whatever the engine does); `test_supabase_migrations_apply_live.py` (skips without Postgres, everywhere); `test_supabase_migrations_structure.py` (13 tests on dormant DDL); `test_broker_app.py::test_default_app_boots_against_real_bedrock` (cannot run without `/data`); `test_lanes.py::test_build_lane_pool_builds_exactly_six_lanes` (true in tests, false in the documented deployment with profiles in one region, `config.py:398-400`).

**Missing:** (1) a cross-package `HttpLedgerClient` <-> embedded world app contract test (INFRA-25); (2) any test that `llm_calls.tick`/`game_day` is populated or that `export_game_day` returns `llm_calls` rows (INFRA-02); (3) Build Spec 4.5 back-pressure bound for a broke resident across ticks (INFRA-03); (4) a test that the checkpoint mirror writes to the configured file (INFRA-01); (5) an env-template boot test (INFRA-04); (6) a Kimi text-form tool-call stream test (INFRA-07); (7) Build Spec 4.3 table-coverage test (INFRA-17); (8) a test that a broker restart does not reset the cap (INFRA-14); (9) a real end-to-end streaming test through `app.py` + `BrokerService` + `SqliteTraceSink` on a file (today the HTTP-level tests use `InMemorySink` and the sink tests use hand-built rows); (10) a test that the mock gateway's stream matches the bare-delta shape of real Bedrock by default (INFRA-23).

---

## 8. Open questions for the operator

1. **Lanes.** `broker/config.py:398-400` records that this account has Kimi and DeepSeek profiles in ap-south-1 only (200 RPM). Is that still true? Every throughput number in DESIGN §9/§11 and the 250-resident plan assumes 600.
2. **Firecracker.** The runner refuses the backend and nothing has ever booted on KVM. Keep the track (and budget a metal-host day to prove it), or delete `app/infra/firecracker/` and make "250 processes on one host" the design?
3. **Who pays for housekeeping.** DESIGN §4 bills the learning centre to the resident; the awake-interval batch now runs it mid-morning at up to 4 cents a call. Keep, move to sleep only, or bill a `learning` account?
4. **Reasoning (F1).** Seam-side `thinking` injection (legal, layering cost) or broker-side knob? And is a $1 live probe of `reasoning_config` on Kimi/DeepSeek authorised?
5. **Raw payload retention.** Archiving every request/response in full is roughly 80-150 KB gzipped-to-~20 KB per call, i.e. ~40 MB per 1,000 calls; for a 250-resident 20-day run that is tens of GB in S3. Acceptable, or keep the 64 KB inline cap plus full system-prompt archive only?
6. **Secrets.** Who owns `AUTH_SHARED_SECRET`/`OPERATOR_TOKEN` generation for a run; is an env file under `/opt/kasukabe/app/infra/env/` (600, `kasukabe:kasukabe`) the agreed home?
7. **Migrations.** Delete `supabase/migrations/` (dormant Postgres) or keep extending it in lockstep? Today it costs three edits per schema change and tests that never run.
8. **Run artifact layout.** Proposed: `runs/<run_id>/{manifest.json, kasukabe.db, checkpoints/, engines/<day>.tar, exports/parquet/game_day=N/}` plus the Litestream replica. Agree on the directory before INFRA-09 lands so the report generator and observatory read one layout.
