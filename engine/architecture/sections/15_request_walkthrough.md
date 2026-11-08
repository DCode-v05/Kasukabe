# The End-to-End Request Walkthrough

> One `/execute` call, traced through the whole engine. Each step names the chapter that owns the detail — this is the connective tissue between the per-subsystem deep-dives. For the visual version, see the `/execute` sequence diagram in [Server / Bootstrap / Auth](#sec-30-integration-server-md); for the vocabulary, see the [Glossary](#sec-90-glossary-md).

A single user message becomes a stream of typed `Block` frames by passing through roughly fourteen stations. Read it top-to-bottom as "a message travels through X, then Y, then Z."

## The path of one turn

1. **Edge ingress.** The product frontend opens `POST /execute` (an SSE request) carrying the user message. The RequestId middleware stamps an `x-request-id`, and the Unicode sanitizer strips dangerous codepoints before anything else touches the body. — [Server / Bootstrap / Auth](#sec-30-integration-server-md), [Learning / Security / Raven](#sec-58-learning-security-raven-md)

2. **Authentication.** The `x-engine-key` is SHA-256 hashed and constant-time compared against the active (and any pending-rotation) key. A bad key fails closed here. — [Server / Bootstrap / Auth](#sec-30-integration-server-md)

3. **Stream + turn setup.** The server registers the stream, spawns the turn task, and immediately opens the `text/event-stream` so the client sees a live channel before any model work starts. — [Server / Bootstrap / Auth](#sec-30-integration-server-md)

4. **Thread binding.** The Coordinator routes the message to its persistent conversation **thread** (creating one or reusing the open one), acquiring the per-thread turn lock so two turns can't interleave. — [Agent Loop & Threading](#sec-40-engine-core-loop-md)

5. **Context assembly.** The `ExecutionThread` builds the model context — charter, retrieved memory, and conversation history — laid out behind the **frozen prompt-cache prefix** so the cached tokens stay stable across the turn. — [Agent Loop & Threading](#sec-40-engine-core-loop-md), [Context & Compaction](#sec-42-engine-core-context-compaction-md), [Memory System](#sec-54-memory-md)

6. **The agentic loop.** Tools were frozen at thread init; the loop now calls the model through the herald gateway, which forwards to **citadel** (`POST /v1/messages`, streaming). — [Agent Loop & Threading](#sec-40-engine-core-loop-md), [Herald (LLM Gateway)](#sec-52-herald-md)

7. **Model stream.** citadel streams token deltas and tool-use requests back; herald relays the live quota/entitlement badge alongside the content. — [Herald (LLM Gateway)](#sec-52-herald-md)

8. **Tool execution.** Each tool-use is dispatched through the capability registry: shell/code runs go to the sandbox's **bypass-immune permission pipeline** and then one ring deeper into `bwrap + seccomp + Landlock`; connector calls go through the asset directory / MCP. — [Utility / Activity / Infra](#sec-60-utility-utils-infra-md), [Sandbox (Secure Execution)](#sec-50-sandbox-md), [Asset Directory & Channels](#sec-56-asset-directory-channel-md)

9. **Hooks.** Lifecycle hooks fire around the tool boundary (pre-tool gates, post-tool transforms) and at turn checkpoints. — [Hooks Subsystem](#sec-44-engine-core-hooks-md)

10. **Event fold.** Internal events emitted throughout the turn are folded by the block fan-sink into the **27-type Block wire contract**, each frame assigned a per-thread sequence id. — [Sub-Agents, Tools & Blocks](#sec-46-engine-core-agents-tools-blocks-md), [Block Wire Contract](#sec-25-block-contract-md)

11. **Streaming out.** Block frames flow to the client over the open SSE channel as they are produced. — [Server / Bootstrap / Auth](#sec-30-integration-server-md), [Block Wire Contract](#sec-25-block-contract-md)

12. **Compaction (as needed).** If the context approaches budget mid-turn, compaction (snip → microcompact → context-collapse) reclaims room **without** disturbing the cache prefix. — [Context & Compaction](#sec-42-engine-core-context-compaction-md)

13. **Turn close.** When the loop returns, the server emits the terminal `done` Block followed by the `DONE` sentinel and closes the stream; the thread returns to `COMPLETED`/`IDLE`. — [Server / Bootstrap / Auth](#sec-30-integration-server-md), [Block Wire Contract](#sec-25-block-contract-md)

14. **Offline tail.** After the turn, the learning pipeline distils session signals into durable memory, and raven records the telemetry — neither is on the request's critical path. — [Learning / Security / Raven](#sec-58-learning-security-raven-md), [Memory System](#sec-54-memory-md)

## The boundaries it crosses

A single turn touches the **edge** (auth, sanitize, stream), the **orchestration core** (thread, loop, hooks, compaction, block fold), the **intelligence plane** (herald → citadel, memory), and the **capability plane** (sandbox, connectors) — and ends back at the edge. The trust boundary that matters most sits at step 8: everything before it is the **trusted** engine process; the LLM-generated code that runs inside the tool call is **untrusted** and is confined to the inner ring. See [Security & Deployment](#sec-70-security-deployment-md) for the full trust model.
