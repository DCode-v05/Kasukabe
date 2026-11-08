## Glossary — The Engine's Vocabulary

The codebase uses internal codenames and domain terms heavily. This is the decoder ring.

### External systems & codenames
- **citadel** — the external **model gateway** (`CDT_URL`/`CDT_KEY`). Holds all provider keys, fans out to Anthropic/OpenAI/Gemini, and enforces quota/budget/subscription. The engine speaks the Anthropic Messages wire shape to it and holds no provider keys itself (see [System Context](#sec-10-system-context-md)).
- **herald** — the engine's *internal* codename for its LLM-adapter subsystem (the client that talks to citadel). Scrubbed from all response bodies (see [Herald](#sec-52-herald-md)).
- **raven** — the engine's *internal* codename for its telemetry/observability subsystem. Persists to neutral `obs_*` tables so the name never surfaces in SQLite/backups. Scrubbed from response bodies (see [Learning/Security/Raven](#sec-58-learning-security-raven-md)).
- **bap-engine** — the **orchestrator** that provisions engine containers and rotates keys (via `x-admin-key`) (see [Server/Bootstrap/Auth](#sec-30-integration-server-md)).
- **bap-backend** — the **connector control plane** (formerly the asset-router): brokers external-connector OAuth and drives the engine's connection lifecycle synchronously over the SDK `/assets/*` API, reading each call's ack (no webhooks) (see [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).
- **Product frontend** — the UI that calls `/execute` and renders Block frames + crafts (see [System Context](#sec-10-system-context-md)).

### Core runtime concepts
- **Thread** (`ExecutionThread`) — a persistent per-task conversation runtime, reused across messages. Identity: `thread_id` (renamed from `task_id` in v2; rename catalog in [System Context](#sec-10-system-context-md)) (see [Agent Loop & Threading](#sec-40-engine-core-loop-md)).
- **Coordinator** — the single entry hub: routes a user message to a thread, owns the event emitter and pause/resume (see [Agent Loop & Threading](#sec-40-engine-core-loop-md)).
- **Pinned thread** — a thread the product layer owns (`Coordinator.set_thread_pinned` / `is_thread_pinned`): exempt from the reaper and from waiting-period expiry, so its pending questions stay answerable indefinitely. The flag lives on the in-memory index entry and is persisted through `conversation_store.set_pinned` so it survives a restart (see [Agent Loop & Threading](#sec-40-engine-core-loop-md)).
- **Block** — the single SSE envelope on `/execute` (`{id, type, thread_id, ts, payload}`); 27 block types folded from ~60 internal events (canonical wire contract in [Block Wire Contract](#sec-25-block-contract-md)).
- **Turn / agent loop** — one model→tools→model iteration; `agent_loop` runs up to 200 turns (`MAX_AGENT_TURNS`) (see [Agent Loop & Threading](#sec-40-engine-core-loop-md)).
- **Charter** — a planning DAG (`Node`/`Edge`) representing a mission; goals are injected as `<system-reminder>`, never as new tools (see [Sub-Agents/Tools/Blocks](#sec-46-engine-core-agents-tools-blocks-md)).
- **Craft** — an interactive UI surface authored by the agent via `<craft>` tags (forms, questions); `craft_wait` parks the turn until the user answers (see [Sub-Agents/Tools/Blocks](#sec-46-engine-core-agents-tools-blocks-md)).
- **Sub-agent** — an isolated child agent (`AgentType` EXPLORE/PLAN/VERIFY/GENERAL) with its own history, budgets, and filtered tools; depth-capped at 1 level (see [Sub-Agents/Tools/Blocks](#sec-46-engine-core-agents-tools-blocks-md)).
- **Fork** — a cache-sharing sub-agent that prepends the parent's message prefix to reuse the Anthropic prompt cache (see [Context & Compaction](#sec-42-engine-core-context-compaction-md)).

### Context & compaction
*(All canonical in [Context & Compaction](#sec-42-engine-core-context-compaction-md).)*
- **Compaction** — reducing context when over ~80% of the window; LLM-summarized or mechanical, with a circuit breaker.
- **Microcompaction** — time/idle-driven clearing of stale tool-result content (not token-driven).
- **Context collapse** — mechanical, no-LLM staging + committing of low-importance message spans.
- **Snip / cached snip** — replacing provably-redundant tool results with stubs; "cached snip" does it via server-side `cache_edits` so the prompt-cache prefix stays byte-identical.
- **Prompt-cache prefix stability** — the load-bearing invariant: tools are frozen once registered and mutations are avoided so the Anthropic KV cache keeps hitting.

### Memory
*(Retrieval, hybrid scoring, and decay semantics canonical in [Memory](#sec-54-memory-md).)*
- **Working memory** — the ephemeral (24h) temporal event log of a task (in-memory or SQLite backend).
- **Episodic memory** — durable "what happened" KDV (Key/Description/Value) episodes; tiered gzip archival ≥30d.
- **Knowledge store** — durable facts (user-stated, conf 1.0), inferences (≤0.8, decaying), and processes; bitemporal, never hard-deleted.
- **Social graph** — people as nodes (user = primary), with attributes and three-perspective relationship edges.
- **Soul** — the agent's *character*: **Core Soul** (immutable, catalog-loaded, shared) + **Evolved Soul** (per-user epigenetic traits that "express" after confident evidence).
- **Consciousness** — the session-scoped identity bundle (Core Soul + Evolved Soul + user persona + recent memory) injected into the system prompt.
- **Learning signal** — a cheap, deterministically captured record of a turn that the Learning Centre later distills into durable memory (see [Learning/Security/Raven](#sec-58-learning-security-raven-md)).

### Capability & connectors
- **Utility Directory (UD)** — the registry of agents/skills/tools; platform definitions in an in-memory Catalog, user state in SQLite (see [Sub-Agents/Tools/Blocks](#sec-46-engine-core-agents-tools-blocks-md)).
- **ToolDef / ToolRegistry** — the runtime tool format and dispatch registry; `partition_tool_calls` plans safe-parallel vs serial execution (see [Sub-Agents/Tools/Blocks](#sec-46-engine-core-agents-tools-blocks-md)).
- **Skill** — an instruction + optional code recipe; promoted to `active` after `occurrence_count ≥ 3 AND confidence ≥ 0.8` (see [Learning/Security/Raven](#sec-58-learning-security-raven-md)).
- **Asset / connection** — an external service connection (`connection_id`, renamed from `ref_id` in v2; rename catalog in [System Context](#sec-10-system-context-md)); credentials MultiFernet-encrypted (credential lifecycle in [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).
- **Provenance** — the authority a connection or server_registry row carries. Connection-authority ladder: **engine < user < managed**; `septemberai` is a separate SOURCE tier (September's own hosted servers, dormant registry). A server can hold several ACTIVE connections at once across `(provenance, account_ref)` (see [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).
- **Per-tool permission** — the DB-backed `connection_permissions` policy per `(connection_id, action_name)`: a 3-state `behavior` (allow/ask/deny) + an `enabled` on/off switch + a managed `locked` flag, defaulting from the action's MCP `readOnlyHint` (see [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).
- **MCP** — Model Context Protocol; the engine is a JSON-RPC 2.0 client to remote HTTP/SSE MCP servers; remote tools surface as `ad:{server}.{action}` (see [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).
- **Action pinning** — rejecting an MCP tool whose schema hash drifts from the recorded pin (prompt-injection defense) (see [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).
- **Channel** — a pinned **read-only** polled query against a connected asset, fanned to craft UIs over SSE; deduped by `channel_id` (see [Asset Directory & Channels](#sec-56-asset-directory-channel-md)).

### Security & sandbox
- **bwrap (bubblewrap)** — the inner sandbox that runs untrusted agent code in fresh namespaces (see [Sandbox](#sec-50-sandbox-md)).
- **seccomp / Landlock** — the inner syscall blocklist and filesystem ACL layered inside bwrap (see [Sandbox](#sec-50-sandbox-md)).
- **Bypass-immune tier** — the first permission-pipeline steps (hardcoded denials, AST bash analysis, dangerous-path/removal gates) that no rule/mode/hook/approval can override (see [Sandbox](#sec-50-sandbox-md)).
- **subprocess_env** — the credential-scrubbing helper that hands every spawn a fresh env without cloud secrets (never mutates `os.environ`); contract canonical in [Security & Deployment](#sec-70-security-deployment-md).
- **Trusted-zone proxy** — the optional egress proxy that gates network per-binary/per-host and TLS-MITM-injects credentials (defense ladder in [Security & Deployment](#sec-70-security-deployment-md)).

### Infrastructure
- **brain.sqlite** — the single WAL-mode SQLite DB holding all engine state; sqlite-vec for vectors, FTS5 for keyword search (table catalog in [Data Model](#sec-20-data-model-md)).
- **`x-engine-key`** — the product auth credential (`sk-sept-…`, stored as a hash, hot-rotatable) (see [Server/Bootstrap/Auth](#sec-30-integration-server-md)).
- **`x-admin-key`** — the orchestrator-only credential for key rotation (see [Server/Bootstrap/Auth](#sec-30-integration-server-md)).
- **`x-request-id`** — the server-generated correlation id propagated to logs and Block ids (see [Server/Bootstrap/Auth](#sec-30-integration-server-md)).
- **EngineState** — the flat holder of ~40 wired subsystem references built by `bootstrap.py`; `is_operational ≡ coordinator is not None` (see [Server/Bootstrap/Auth](#sec-30-integration-server-md)).
