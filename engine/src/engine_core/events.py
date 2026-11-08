"""Consolidated event types for streaming output.

55 event types. Events flow from Thread -> Coordinator -> Product Layer.
The thread emits events as it executes; the coordinator streams them
to all registered consumers via the EventEmitter.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Literal


SpawnSite = Literal[
    "sandbox_exec",
    "sandbox_background",
    "sandbox_pty",
    "sandbox_isolated",
]

logger = logging.getLogger(__name__)


class GapType(str, Enum):
    """Structured gap types for product layer routing."""

    INFO_GAP = "info_gap"
    ACTION_GAP = "action_gap"
    ASSET_REQUIRED = "asset_required"
    ASSET_ACTION = "asset_action"
    PERMISSION_REQUEST = "permission_request"
    ASSET_STATUS = "asset_status"


@dataclass
class TextDelta:
    """A chunk of streaming text from the LLM."""

    thread_id: str
    text: str
    citations: tuple = ()


@dataclass
class SourceDiscovered:
    """A source registered for this turn (citations gate on) → folds to a
    ``"source"`` block.

    ``data`` is the kind-specific DISPLAY payload only:
      * memory → ``{"points": [str, ...]}``
      * asset  → ``{"name": str, "info": str}``
      * web    → ``{"links": [{"title": str, "url": str}, ...]}``

    The engine-private ref/score live in the ``SourceRegistry``, never here.
    ``turn_id`` scopes the block id (``source:{turn_id}:{handle}``) so handles
    never collide across turns; the model still cites the short ``handle``.
    """

    thread_id: str
    handle: str
    kind: str
    data: dict
    turn_id: str = ""


@dataclass
class ThreadLifecycle:
    """Thread started, completed, or failed."""

    thread_id: str
    status: str
    model_id: str = ""


@dataclass
class ToolExecution:
    """A tool was called or returned a result.

    Unified: covers platform tools, skills, asset actions, and parallel batches.
    """

    thread_id: str
    tool_name: str
    source: str
    phase: str
    params: dict = field(default_factory=dict)
    result: str = ""
    duration: int = 0
    status: str = ""
    parallel_batch_id: str = ""
    permission_tier: int = 0
    chain_id: str = ""


@dataclass
class NodeProgress:
    """Charter node status changed or todo list initialized/updated."""

    thread_id: str
    charter_id: str = ""
    node_id: str = ""
    label: str = ""
    status: str = ""
    output_preview: str = ""
    items: list[dict] | None = None
    is_replan: bool = False


@dataclass
class CraftRequest:
    """The engine is asking the user for input — folds to a ``craft`` block.

    ``format`` declares the rendering intent (single-choice, multi-choice,
    yes/no). Each surface — web, CLI, voice, Slack — renders the intent
    however it wants. The frontend always offers a free-text "Other" input
    below the structured options; the engine never lists "Other" in ``options``.

    Permission prompts reuse this event type: ``suggested_rule`` is set and the
    client renders a "remember this decision" affordance. Permission prompts use
    ``format='confirm'`` (default).

    ``block_id`` is the v2 waiting-block id minted by the coordinator's
    ``await_block_input``. It is BOTH the key in ``pending_inputs`` AND the id
    the craft block carries on the wire, so the client echoes it back as
    ``block_input.block_id`` and ``resume_block`` resolves the exact waiter.
    """

    thread_id: str
    question: str
    gap_type: GapType | None = None
    asset_hint: str = ""
    format: str = "confirm"
    options: list[dict] = field(default_factory=list)
    suggested_rule: str = ""
    block_id: str = ""
    wait: bool = True

    @property
    def is_permission(self) -> bool:
        return bool(self.suggested_rule)


@dataclass
class CraftAuthored:
    """The engine AUTHORED a UI component — folds to a ``craft`` block.

    Distinct from ``CraftRequest`` (the engine ASKING the user): this is a
    self-contained component the model wrote inside a ``<craft>`` tag in its text
    stream, which ``craft_parser`` turns into this event. Both fold to the one
    ``craft`` block type; the product's single renderer handles both.

    ``block_id`` is minted by the parser per craft (stable across the streamed
    content of one craft, distinct between crafts in a turn) so a ``wait:true``
    craft is resume-addressable via ``block_input`` exactly like ``CraftRequest``.
    ``content`` is appended by ``block_id`` as the body streams (append-by-id,
    like text); ``final`` marks the ``</craft>`` close and, when ``wait`` is set,
    triggers ``done.status="awaiting"``.
    """

    thread_id: str
    block_id: str
    key: str
    surface: str
    format: str
    wait: bool = False
    title: str = ""
    language: str | None = None
    content: str = ""
    version: int = 1
    subscribe: list[dict] = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    final: bool = False


@dataclass
class SubAgentStart:
    """A sub-agent has been spawned within a thread."""

    thread_id: str
    agent_id: str
    agent_name: str
    task_description: str
    chain_id: str = ""
    depth: int = 0
    parent_chain_id: str = ""
    is_fork: bool = False
    fork_label: str = ""
    agent_type: str = "general"
    model_id: str = ""


@dataclass
class SubAgentDone:
    """A sub-agent has completed its work."""

    thread_id: str
    agent_id: str
    result_summary: str
    chain_id: str = ""
    depth: int = 0
    cache_hit_rate: float = 0.0
    fork_label: str = ""
    exit_reason: str = (
        "completed"
    )


@dataclass
class SubAgentForkMetrics:
    """Per-fork cost and cache-share telemetry. Emitted alongside SubAgentDone
    when fork_label is non-empty so analytics consumers can attribute savings
    to specific fork sites (compaction, summarizer, memory extraction).

    Mirrors tengu_fork_agent_query in the reference (forkedAgent.ts:631-689).
    """

    thread_id: str
    agent_id: str
    fork_label: str
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cache_hit_rate: float
    cost_usd: float = 0.0
    cost_saved_usd: float = 0.0
    chain_id: str = ""
    depth: int = 0


@dataclass
class SubAgentCancelled:
    """A sub-agent's cancellation token fired (parent cancel or explicit cancel())."""

    thread_id: str
    agent_id: str
    agent_name: str
    reason: str = ""
    chain_id: str = ""
    depth: int = 0


@dataclass
class SubAgentBackgrounded:
    """A sub-agent transitioned from foreground to background execution.

    Emitted when run_async() is used or when a slow sync agent is lifted into
    the background after PROGRESS_THRESHOLD_MS. Parent gets the thread_id to
    retrieve the result later.
    """

    thread_id: str
    agent_id: str
    agent_name: str
    background_thread_id: str
    chain_id: str = ""
    depth: int = 0


@dataclass
class SubAgentProgress:
    """Periodic progress heartbeat from a running sub-agent.

    Emitted at the end of each turn so observers (UI, metrics, parent agent)
    can gauge work-in-flight without polling. Mirrors ProgressTracker in
    agentToolUtils.ts: tool use count, cumulative tokens, most recent tool
    activities as a short ring buffer.
    """

    thread_id: str
    agent_id: str
    agent_name: str
    turn: int
    tool_use_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    recent_activities: list[str] = field(default_factory=list)
    chain_id: str = ""
    depth: int = 0


@dataclass
class SubAgentIdle:
    """A sub-agent has been idle (no tool call / LLM call) for longer than
    the configured idle_threshold_ms. Emitted once per idle stretch — parents
    can decide to kill, background, or keep waiting.
    """

    thread_id: str
    agent_id: str
    agent_name: str
    idle_ms: int
    last_activity: str = ""
    chain_id: str = ""
    depth: int = 0


@dataclass
class SubAgentSummary:
    """A running sub-agent has produced a short progress summary.

    Generated by the background summarizer as a cache-sharing fork of the
    parent conversation. 1-2 sentences suitable for UI display.
    """

    thread_id: str
    agent_id: str
    agent_name: str
    summary: str
    turn: int = 0
    chain_id: str = ""
    depth: int = 0


@dataclass
class CharterCreated:
    """A charter was created by the planner."""

    thread_id: str
    charter_id: str
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)


@dataclass
class ExecutionSummary:
    """Emitted after task completion — full execution telemetry."""

    thread_id: str
    status: str
    duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    estimated_cost_usd: float = 0.0
    tool_calls_count: int = 0
    model_id: str = ""
    exit_reason: str = "completed"
    last_model_stop: str = ""
    num_turns: int = 0
    chain_id: str = ""
    depth: int = 0
    turn_id: str = ""
    turn_counter: int = 0
    quota: dict | None = None


@dataclass
class ReplanRequested:
    """Thread detected a replan trigger."""

    thread_id: str
    charter_id: str = ""
    reason: str = ""


@dataclass
class AssetRequiredEvent:
    """Thread needs an unconnected service - product layer handles connection flow.

    ``connection_id`` is the connection row to complete (reused if one already
    exists for this server, freshly minted via ``AssetDirectory.init_connect``
    otherwise) - None only if connection resolution itself failed.
    ``oauth_url`` is the ready-to-render authorize link for that connection;
    None when the server isn't OAuth-based (e.g. API-key auth) or has no
    oauth_config on file, in which case the product layer falls back to its
    own connect UI keyed off ``connection_id``/``server_name``.
    """

    thread_id: str
    server_name: str
    connection_id: str | None = None
    oauth_url: str | None = None


@dataclass
class AssetActionEvent:
    """Telemetry emitted after a connector (asset directory) tool runs.

    Folds to the dedicated ``asset_action`` block on the asset channel. Carries
    short previews of the params and result (truncated at the source) plus the
    wall-clock duration so the product can render a compact "ran X on server Y"
    row. ``status`` is ``"ok"`` when the action returned and ``"error"`` when it
    raised (auth expiry or any other failure).
    """

    thread_id: str
    server_name: str
    action_name: str
    connection_id: str | None = None
    params_preview: str = ""
    result_preview: str = ""
    duration_ms: int = 0
    status: str = ""


@dataclass
class PermissionRequestEvent:
    """A first-class per-tool approval prompt (behavior 'ask').

    Folds to the dedicated ``permission_request`` block on the asset channel.
    This is the structured render of an approval prompt; the actual reply still
    flows through the existing ``await_user_input`` block-input path (the engine
    does not wait on this event). ``params`` is the full argument object the
    connector tool was invoked with so the client can show what will run.
    ``account_label`` names WHICH account the action would run as: the same
    server can hold several connected accounts, and without it two prompts for
    two different mailboxes are indistinguishable.
    """

    thread_id: str
    server_name: str
    action_name: str
    connection_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    account_label: str = ""


@dataclass
class AssetStatusEvent:
    """A connection state change surfaced during a run.

    Folds to the dedicated ``asset_status`` block on the asset channel. Emitted
    in-run only (e.g. a token expiring mid-run flips the connection to
    ``needs_auth``); out-of-run product-API mutations such as pause/disconnect
    are not surfaced here because they have no thread/emitter and the product
    already knows from its own ack. ``oauth_url`` is the ready-to-render
    reauthorize link when one applies.
    """

    thread_id: str
    server_name: str
    connection_id: str | None = None
    status: str = ""
    needs_auth: bool = False
    oauth_url: str | None = None


@dataclass
class MemoryWriteEvent:
    """Emitted when working memory is written during execution."""

    thread_id: str
    memory_type: str
    key: str
    value_preview: str


@dataclass
class ThinkingDelta:
    """Streams reasoning tokens when super thinking is active."""

    thread_id: str
    text: str


@dataclass
class VoiceDelta:
    """A chunk of speakable narration text (the voice channel) - folds to the
    append-stream ``voice`` block.

    The model authors ``<voice>…</voice>`` spans inline in its reply;
    ``voice_parser`` strips the tags and streams the contents as these deltas.
    The product runs TTS over the accumulated block text; the engine never
    synthesizes audio.

    ``turn_id`` overrides the append-block id scope: empty (the normal turn
    path) means the request correlation id, so a turn's narration appends to
    one ``voice:{rid}`` block. Sub-agents set ``"{rid}-{agent_id}"`` so their
    narration surfaces as its own voice block on the parent stream and the
    product can queue each speaker separately.
    """

    thread_id: str
    text: str
    turn_id: str = ""


@dataclass
class MediaAuthored:
    """Base for model-authored media galleries - link-only, never bytes.

    The model authors ``<image url="…">`` / container ``<image>…<item url="…">…
    </image>`` tags inline; ``voice_parser`` turns each complete tag into one of
    the subclasses below (one block = one gallery = one id). ``block_id`` is
    parser-minted (``{kind}:{thread}:{turn_key}:{n}``) so several media blocks
    in one turn never collide under replace-by-id. Every item URL passed the
    per-thread provenance registry (``media_registry``) before this event was
    emitted; an unproven URL never reaches the wire.

    Not in FOLD itself - only the concrete subclasses fold.
    """

    thread_id: str
    block_id: str
    title: str = ""
    items: list[dict] = field(default_factory=list)


@dataclass
class ImageAuthored(MediaAuthored):
    """A model-authored image gallery - folds to an ``image`` block."""


@dataclass
class AudioAuthored(MediaAuthored):
    """A model-authored audio set - folds to an ``audio`` block."""


@dataclass
class VideoAuthored(MediaAuthored):
    """A model-authored video set - folds to a ``video`` block."""


MEDIA_EVENTS: dict[str, type[MediaAuthored]] = {
    "image": ImageAuthored,
    "audio": AudioAuthored,
    "video": VideoAuthored,
}
MEDIA_KINDS: tuple[str, ...] = tuple(MEDIA_EVENTS)


@dataclass
class StrugglingDetected:
    """Thread detected execution is struggling — auto-plan/replan triggered.

    Emitted with full diagnostic context so the product layer can display
    a meaningful notification (not just "something went wrong").
    """

    thread_id: str
    reason: str
    action_taken: str
    turn_count: int
    consecutive_errors: int
    turns_without_text: int
    turns_since_checkpoint: int
    repeated_pattern: str


@dataclass
class PreToolUseHookExecuted:
    """Pre-tool hook sweep ran before a tool call.

    Emitted every time the agent loop's pre-tool hook registry has at
    least one hook registered and a tool call fires. Carries one
    ``hook_outcomes`` entry per hook (registration order). ``denied``
    is True if any hook returned ``decision="deny"``; ``allowed`` if
    any hook returned ``decision="allow"`` and no hook denied.
    ``prevented`` mirrors stop-hook semantics — the loop will exit
    after the current tool batch.
    """

    thread_id: str
    tool_name: str
    tool_use_id: str
    denied: bool
    allowed: bool
    prevented: bool
    hook_outcomes: list[dict]


@dataclass
class PostToolUseHookExecuted:
    """Post-tool hook sweep ran after a tool result was produced.

    Emitted every time the agent loop's post-tool hook registry has at
    least one hook registered and a tool call completes. ``prevented``
    is True if any hook set ``prevent_continuation``; the loop will
    exit after the current tool batch. ``context_count`` is the number
    of non-empty ``additional_context`` strings collected from hooks.
    """

    thread_id: str
    tool_name: str
    tool_use_id: str
    prevented: bool
    context_count: int
    hook_outcomes: list[dict]


@dataclass
class StopHookExecuted:
    """Stop-hook sweep ran at a turn boundary.

    Emitted every time the agent loop's stop-hook registry has at least
    one hook registered and the sweep runs. Carries one ``hook_outcomes``
    entry per hook (in registration order) so observers can see which
    hooks prevented, which blocked, which timed out, and how long each
    took. ``trigger`` distinguishes the end-of-turn call site
    ("after_end_turn") from the post-tool-results call site
    ("after_tool_use") — deliberately namespaced away from ``StopReason``
    values so consumers don't conflate "where in the loop" with "why
    the model stopped." ``retry_active`` is True when the loop is
    currently inside a blocking-error retry turn.
    """

    thread_id: str
    turn_number: int
    trigger: str
    prevented: bool
    blocked: bool
    retry_active: bool
    hook_outcomes: list[dict]


@dataclass
class CompactionEvent:
    """Context window compaction fired — messages were compressed."""

    thread_id: str
    messages_before: int
    messages_after: int
    tokens_before: int
    tokens_after: int
    method: str
    chain_id: str = ""
    turn_id_before: str = ""
    turn_id_after: str = ""


@dataclass
class ReactiveCompactionEvent:
    """Reactive compaction fired in response to API context overflow."""

    thread_id: str
    tier: int
    strategy: str
    tokens_before: int
    tokens_after: int
    messages_before: int
    messages_after: int
    overflow_attempt: int
    made_progress: bool


@dataclass
class MicrocompactEvent:
    """Time-based or forced microcompact fired — stale tool results cleared.

    Emitted by CompactionOrchestrator whenever maybe_microcompact() returns
    a non-None MicrocompactResult. Distinct from CompactionEvent because
    microcompact operates on tool_result *content* (surgical) while
    CompactionEvent describes whole-message drop/summarize passes (broad).

    ``gap_minutes`` is rounded to the nearest integer at emission to match
    the src reference's ``Math.round(gapMinutes)`` telemetry contract — the
    internal trigger math uses a float for precision, but analytics
    consumers see a clean integer.
    """

    thread_id: str
    mode: str
    gap_minutes: int
    gap_threshold_minutes: float
    tools_cleared: int
    tools_kept: int
    tokens_saved: int
    keep_recent: int


@dataclass
class CompactionPtlRetryEvent:
    """A PTL retry attempt fired during LLM summarization.

    Emitted each time the summarization retry loop drops oldest API-round
    groups and retries after a prompt-too-long condition. Two trigger paths:
    ``pre_check`` (token estimate exceeded context_window before the LLM call)
    and ``api_overflow`` (the LLM returned a context overflow error).
    """

    thread_id: str
    attempt: int
    max_retries: int
    trigger: str
    token_gap: int | None
    messages_before: int
    messages_after: int


@dataclass
class CompactionPtlFailedEvent:
    """PTL retries exhausted — falling through to mechanical compaction.

    Emitted when the summarization retry loop cannot recover from the
    prompt-too-long condition. ``reason`` disambiguates the failure mode.
    """

    thread_id: str
    reason: str
    attempts: int
    final_message_count: int


@dataclass
class CacheHitRatioAlert:
    """A cache-health alert raised by the per-invocation CacheHitMonitor.

    ``alert_type`` carries a ``CacheAlertType`` value: ``"ratio_low"`` when the
    turn's cache hit ratio fell below 0.5 from turn 2 onward, ``"prefix_unstable"``
    when cache_read dropped both more than 10% and more than 2000 tokens against
    the previous turn. A single turn can raise both, in which case two events are
    emitted for that turn.

    ``prev_cache_read`` and ``drop_pct`` are populated only for ``"prefix_unstable"``;
    ``message`` is the same human-readable line the engine logs, and for a drop it
    includes the monitor's cause attribution (model change, tool set change, or
    TTL expiry).
    """

    thread_id: str
    alert_type: str
    turn: int
    cache_ratio: float
    cache_read: int
    cache_create: int
    uncached: int
    message: str
    prev_cache_read: int | None = None
    drop_pct: float | None = None


@dataclass
class TokenBudgetProgress:
    """Per-turn progress signal while a token budget is active.

    Emitted every turn the feature is enabled and a budget is set, so TUIs
    and dashboards can render live consumption without polling the tracker.
    """

    thread_id: str
    turn_tokens: int
    budget: int
    pct: int
    continuation_count: int


@dataclass
class TokenBudgetContinuation:
    """A nudge was injected to continue the turn loop under the budget.

    Carries the exact nudge text so observers can log what instruction the
    engine sent back to the model.
    """

    thread_id: str
    continuation_count: int
    pct: int
    turn_tokens: int
    budget: int
    nudge_message: str


@dataclass
class TokenBudgetCompleted:
    """The token budget loop ended. Fires only when there was at least one
    continuation OR when diminishing-returns early-stop triggered (matches
    src/query/tokenBudget.ts:78 — no event noise for passive no-op stops).
    """

    thread_id: str
    continuation_count: int
    pct: int
    turn_tokens: int
    budget: int
    diminishing_returns: bool
    duration_ms: int


@dataclass
class RoutingDecision:
    """The dispatch: how one input was triaged into work.

    ``segments`` carries one entry per piece of work found in the input, each
    with its verbatim extract, verdict, and target. Segments verdicted
    ``claimed`` or ``contested`` are NOT executed - they ride out here for the
    caller to resolve and resubmit against an explicit thread.

    This is an engine-origin report: it speaks for the engine rather than for
    any one thread, so ``thread_id`` is empty.
    """

    thread_id: str
    reasoning: str
    active_thread_count: int
    segments: list[dict] = field(default_factory=list)


@dataclass
class ThreadCreated:
    """A new persistent thread was created."""

    thread_id: str
    reason: str


@dataclass
class ThreadArchived:
    """A thread was deleted after inactivity."""

    thread_id: str
    turn_count: int
    duration_hours: float


@dataclass
class IncrementalFlush:
    """LC incremental flush completed for a thread."""

    thread_id: str
    slice_number: int
    entries_flushed: int


@dataclass
class ModelFallback:
    """Primary model hit repeated 529 overload — execution switched to fallback."""

    thread_id: str
    original_model: str
    fallback_model: str
    consecutive_529s: int


@dataclass
class PersistentRetryWait:
    """Heartbeat during persistent retry — keeps callers aware session is alive."""

    thread_id: str
    status_code: int
    remaining_s: float
    attempt: int


@dataclass
class MediaErrorRecovery:
    """Problematic media content was stripped and the API call will be retried."""

    thread_id: str
    media_type: str
    error_kind: str
    blocks_stripped: int
    retry_attempt: int


@dataclass
class SubprocessSpawnedEvent:
    """Emitted whenever a child process is spawned via ``subprocess_env()``.

    Carries scrub metadata so auditors can prove that every spawn went
    through the scrub helper and see which tier + how many vars were
    stripped. Emitted by the spawn-observer hook registered by bootstrap.
    """

    spawn_site: SpawnSite
    mode: Literal["off", "default", "strict"]
    scrubbed_count: int
    has_proxy_env: bool


@dataclass
class InterruptibleStateChanged:
    """Emitted when the interruptibility of currently-executing tools flips.

    interruptible == True  => every currently-executing tool is CANCEL
                              (safe to Ctrl+C; UI may show interruptible spinner)
    interruptible == False => nothing executing, or at least one BLOCK tool
                              is mid-flight (spinner shows non-interruptible)
    """

    thread_id: str
    interruptible: bool
    tool_count: int


@dataclass
class FileRestorationEvent:
    """A single file was restored into context after compaction."""

    path: str
    tokens: int
    truncated: bool
    thread_id: str = ""


@dataclass
class FileRestorationFailedEvent:
    """A file failed to restore after compaction."""

    path: str
    reason: str
    thread_id: str = ""


@dataclass
class ActivityStateChanged:
    """Session activity state transitioned (active→idle→away→disconnected).

    Emitted by ActivityManager on every state transition so product-layer
    consumers can react: pause background tasks on idle, flush buffers on
    away, show a reconnect card after away.

    ``idle_seconds``           — wall-clock seconds since the last user input.
    ``tool_calls``             — tool calls recorded since the last active→idle transition.
    ``errors``                 — errors recorded during the same period.
    ``user_active_seconds``    — seconds user was actively engaging within the USER window.
    ``engine_active_seconds``  — seconds the engine was executing tools (sum of durations).
    """

    old_state: str
    new_state: str
    idle_seconds: float
    tool_calls: int = 0
    errors: int = 0
    user_active_seconds: float = 0.0
    engine_active_seconds: float = 0.0


@dataclass
class ReconnectSummaryEvent:
    """User returned from away/disconnected.

    Emitted once by the server layer after ActivityManager.record_user_activity
    returns a structured summary. Frontends render this as the "while you were
    away" payload. ``natural_language`` is either a deterministic recap or, if
    a language model was available, a 1-3 sentence LLM-generated recap.
    """

    previous_state: str
    idle_seconds: float
    tool_calls: int
    errors: int
    user_active_seconds: float
    engine_active_seconds: float
    natural_language: str
    source: Literal["llm", "deterministic"]


@dataclass
class ToolUseSummary:
    """Brief label generated for a completed tool batch.

    Emitted concurrently with the next agent turn after each tool batch
    finishes.  The label is a short git-commit-style string (≤~30 chars)
    suitable for a single-line mobile row display.
    """

    thread_id: str
    summary: str
    turn_number: int = 0
    tool_count: int = 0
    tool_use_ids: list[str] = field(default_factory=list)
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class CompactionCircuitBreakerTripped:
    """Emitted when the circuit breaker trips (CLOSED/HALF_OPEN → OPEN).

    Fired after consecutive failures reach ``circuit_breaker_max_failures``, or
    when a half-open probe fails and the cooldown timer is restarted.
    """

    thread_id: str
    consecutive_failures: int
    last_error: str = ""
    message_count: int = 0
    token_count: int = 0
    reason: str = ""


@dataclass
class CompactionCircuitBreakerProbeAttempt:
    """Emitted when the breaker enters HALF_OPEN and a probe is armed.

    Fired by ``reset_for_new_user_message`` (or ``check_half_open_transition``)
    after the half-open cooldown elapses.
    """

    thread_id: str
    elapsed_seconds: float
    consecutive_failures: int
    reason: str = ""


@dataclass
class CompactionCircuitBreakerReset:
    """Emitted when the circuit breaker fully resets (OPEN/HALF_OPEN → CLOSED).

    Covers: successful probe, per-user-message decay step, context-shrink reset,
    and manual reset.
    """

    thread_id: str
    transition: str
    consecutive_failures: int
    reason: str = ""


@dataclass
class CompactionCircuitBreakerSkip:
    """Circuit breaker is OPEN — compaction was skipped this turn.

    Emitted every time compaction is skipped because the breaker is OPEN.
    ``fallback_strategy`` records what ran instead: "round_drop" when the
    mechanical drop_oldest_rounds fallback succeeded, "" when no fallback was
    possible and compaction was skipped entirely this turn.
    """

    thread_id: str
    message_count: int
    token_count: int
    fallback_strategy: Literal["round_drop", ""] = ""


Event = (
    TextDelta
    | SourceDiscovered
    | ThreadLifecycle
    | ToolExecution
    | NodeProgress
    | CraftRequest
    | CraftAuthored
    | SubAgentStart
    | SubAgentDone
    | SubAgentCancelled
    | SubAgentBackgrounded
    | SubAgentProgress
    | SubAgentIdle
    | SubAgentSummary
    | SubAgentForkMetrics
    | CharterCreated
    | ExecutionSummary
    | ReplanRequested
    | AssetRequiredEvent
    | AssetActionEvent
    | PermissionRequestEvent
    | AssetStatusEvent
    | MemoryWriteEvent
    | ThinkingDelta
    | StrugglingDetected
    | PreToolUseHookExecuted
    | PostToolUseHookExecuted
    | StopHookExecuted
    | CompactionEvent
    | MicrocompactEvent
    | CompactionPtlRetryEvent
    | CompactionPtlFailedEvent
    | CacheHitRatioAlert
    | TokenBudgetProgress
    | TokenBudgetContinuation
    | TokenBudgetCompleted
    | RoutingDecision
    | ThreadCreated
    | ThreadArchived
    | IncrementalFlush
    | ModelFallback
    | PersistentRetryWait
    | MediaErrorRecovery
    | SubprocessSpawnedEvent
    | InterruptibleStateChanged
    | FileRestorationEvent
    | FileRestorationFailedEvent
    | ActivityStateChanged
    | ReconnectSummaryEvent
    | ToolUseSummary
    | CompactionCircuitBreakerTripped
    | CompactionCircuitBreakerProbeAttempt
    | CompactionCircuitBreakerReset
    | CompactionCircuitBreakerSkip
    | ReactiveCompactionEvent
)

EventCallback = Callable[[Event], Awaitable[None]]


class EventEmitter:
    """Async event emitter with error isolation, buffering, and concurrent dispatch."""

    def __init__(self, buffer_size: int = 200) -> None:
        self.callbacks: list[EventCallback] = []
        self.buffer: deque[Event] = deque(maxlen=buffer_size)

    def on(self, callback: EventCallback) -> None:
        self.callbacks.append(callback)

    def off(self, callback: EventCallback) -> None:
        try:
            self.callbacks.remove(callback)
        except ValueError:
            pass

    async def emit(self, event: Event) -> None:
        self.buffer.append(event)
        if not self.callbacks:
            return
        cbs = list(self.callbacks)
        results = await asyncio.gather(
            *(cb(event) for cb in cbs),
            return_exceptions=True,
        )
        for cb, result in zip(cbs, results):
            if isinstance(result, Exception):
                logger.exception(
                    "Event callback %s failed for %s",
                    getattr(cb, "__name__", cb),
                    type(event).__name__,
                    exc_info=result,
                )

    async def replay(self, callback: EventCallback) -> None:
        for event in list(self.buffer):
            try:
                await callback(event)
            except Exception:
                logger.exception("Replay callback error")
