"""The v2 block envelope — one wire shape, 27 types.

The engine's internal bus still carries the ~60 event dataclasses in
``events.py`` unchanged. The fold to blocks happens only here, at the
serialization boundary, replacing ``server.py``'s old per-event serializer.
One ``Block`` envelope, 27 types, one ``seq:``-framed serializer, and a
per-instance id rule that makes "replace by id" reconciliation safe under
concurrency.

Contract: ``architecture/sections/25_block_contract.md`` → "The envelope & frame".

The fold runs through a stateful ``BlockSerializer`` scoped to one stream. It
serves per-turn ids from the request correlation id (server.py's ``rid``), so
every block in one ``/execute`` turn shares a stable prefix without any event
needing to carry it. Events supply only the instance keys (``tool_use_id``,
``agent_id``, ``charter_id``, …).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone

from src.engine_core.events import (
    Event,
    # content
    TextDelta,
    SourceDiscovered,
    ThinkingDelta,
    VoiceDelta,
    ImageAuthored,
    AudioAuthored,
    VideoAuthored,
    ToolExecution,
    ToolUseSummary,
    CraftRequest,
    CraftAuthored,
    # progress / status
    NodeProgress,
    CharterCreated,
    InterruptibleStateChanged,
    StrugglingDetected,
    ActivityStateChanged,
    # trace
    ThreadLifecycle,
    ThreadCreated,
    ThreadArchived,
    ExecutionSummary,
    SubAgentStart,
    SubAgentDone,
    SubAgentCancelled,
    SubAgentBackgrounded,
    SubAgentProgress,
    SubAgentIdle,
    SubAgentSummary,
    SubAgentForkMetrics,
    RoutingDecision,
    ReplanRequested,
    AssetRequiredEvent,
    AssetActionEvent,
    PermissionRequestEvent,
    AssetStatusEvent,
    # system
    CompactionEvent,
    ReactiveCompactionEvent,
    MicrocompactEvent,
    CompactionPtlRetryEvent,
    CompactionPtlFailedEvent,
    CompactionCircuitBreakerTripped,
    CompactionCircuitBreakerProbeAttempt,
    CompactionCircuitBreakerReset,
    CompactionCircuitBreakerSkip,
    TokenBudgetProgress,
    TokenBudgetContinuation,
    TokenBudgetCompleted,
    ModelFallback,
    PersistentRetryWait,
    MediaErrorRecovery,
    PreToolUseHookExecuted,
    PostToolUseHookExecuted,
    StopHookExecuted,
    # audit
    MemoryWriteEvent,
    FileRestorationEvent,
    FileRestorationFailedEvent,
    SubprocessSpawnedEvent,
    ReconnectSummaryEvent,
)


def source_block_id(turn_id: str, handle: str) -> str:
    """Wire id for a ``source`` block: ``source:{turn_id}:{handle}``.

    Inlined when engine_core/sourcing.py was removed for the Kasukabe build.
    The id shape is part of the wire contract and outlives the registry that
    used to mint the handles.
    """
    return f"source:{turn_id}:{handle}"



# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


@dataclass
class Block:
    """One wire frame. ``type`` is one of the 27; ``payload`` is the folded
    event body; ``id`` is derived from the logical instance (see the id rule),
    not minted fresh per emit."""

    type: str
    payload: dict
    thread_id: str = ""
    id: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def sse_frame(block: Block, seq: int, cursor: int | None = None) -> str:
    """Serialize one Block to an SSE frame (``seq:`` framing line).

    ``seq`` is the per-stream delivered-frame counter (used by the SDK for gap
    detection in replay mode); ``cursor`` is the per-thread buffer index (the
    value the SDK passes as ``?after=`` on replay). When ``cursor`` is None only
    the ``seq:`` line is emitted (back-compat); otherwise a ``cursor:`` line is
    written right after ``seq:``, before the ``event: block`` line.
    """
    data = json.dumps(
        {
            "id": block.id,
            "type": block.type,
            # null means the engine itself is speaking rather than any one
            # thread (a dispatch report, an aggregate done). Consumers derive
            # a block's origin from this and nothing else - there is never a
            # sentinel id here, because thread_id is an address: a name that
            # resolves to no thread would break replay and resume.
            "thread_id": block.thread_id or None,
            "ts": block.ts,
            "payload": block.payload,
        },
        default=str,
    )
    if cursor is None:
        return f"seq: {seq}\nevent: block\ndata: {data}\n\n"
    return f"seq: {seq}\ncursor: {cursor}\nevent: block\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# The 27 types + gate buckets (here for the table)
# ---------------------------------------------------------------------------

BLOCK_TYPES: tuple[str, ...] = (
    # content (8)
    "text",
    "reasoning",
    "tool",
    "image",
    "voice",
    "audio",
    "video",
    "craft",
    # sourcing (1)
    "source",
    # progress (2)
    "progress",
    "status",
    # trace (5)
    "thread",
    "summary",
    "sub_agent",
    "routing",
    "replan",
    # asset (1) - one block per domain; folded events set payload.kind
    "asset",
    # system (4)
    "compaction",
    "budget",
    "model",
    "hook",
    # audit (4)
    "memory",
    "file",
    "subprocess",
    "session",
    # outcome (2)
    "error",
    "done",
)
assert len(BLOCK_TYPES) == 27, "block vocabulary must be exactly 27 types"

# "routing" carries the dispatch: which pieces of work the input became, which
# are running, which were handed back for the caller to resolve. That is the
# turn's shape, not telemetry about it, so it is always on - a caller cannot
# make sense of a multiplexed turn without it.
ALWAYS = {"text", "reasoning", "progress", "status", "error", "done", "routing"}
TELEMETRY_TRACE = {"thread", "summary", "sub_agent", "replan"}
TELEMETRY_FULL = {
    "compaction",
    "budget",
    "model",
    "hook",
    "memory",
    "file",
    "subprocess",
    "session",
}
APPEND_TYPES = {"text", "reasoning", "voice"}

# Dedicated asset-domain channel. User-actionable and default-on, gated
# separately from telemetry so the product can subscribe to (or mute) the whole
# asset stream in one flag.
ASSET = {"asset"}


@dataclass
class Gates:
    """Per-stream output gates, parsed from the /execute request top-level fields.

    Each open SSE stream owns one Gates; the sink applies it so a caller only
    receives the block types it opted into. ``ALWAYS`` types always pass
    (except ``reasoning``, which additionally requires ``super_thinking``).
    """

    craft: bool = False
    craft_wait: bool = True
    image: bool = False
    voice: bool = False
    audio: bool = False
    video: bool = False
    tool: bool = True
    telemetry: str = "off"  # "off" | "trace" | "full"
    assets: bool = True  # asset-domain channel (the single asset block, any payload.kind); default on
    super_thinking: bool = False
    sources: bool = False  # gate — citation/source blocks (set from the request citations flag)

    @classmethod
    def from_request(cls, req) -> "Gates":
        """Build the per-stream gates from an /execute request.

        The one place the request -> Gates mapping lives, so a test can assert
        against what the server actually builds instead of re-deriving it. The
        ``voice or voice_primary`` implication in particular has a twin on the
        producer side (``voice_gate_var.set(voice or voice_primary)`` in
        Coordinator.execute_segment); a test that recomputed the expression
        itself asserted on its own arithmetic and would stay green while the
        two sides drifted apart, delivering zero audio on a stream whose
        visible text had already had its <voice> content stripped.
        """
        return cls(
            craft=req.craft,
            craft_wait=req.craft_wait,
            image=req.image,
            voice=req.voice or req.voice_primary,
            audio=req.audio,
            video=req.video,
            tool=req.tool,
            telemetry=req.telemetry,
            assets=req.assets,
            super_thinking=req.super_thinking,
            sources=req.citations,
        )


def passes_gates(block: "Block", g: Gates) -> bool:
    """True if this block type is allowed through the given gates.

    ALWAYS types pass — except ``reasoning``, which is only delivered when
    ``super_thinking`` is on (it is only ever emitted under super_thinking
    upstream; the check is defense-in-depth). Trace/system/audit types follow
    the ``telemetry`` level; content media + craft + tool follow their flag.
    Unknown types fail open so a new block type is never silently dropped.
    """
    t = block.type
    if t in ALWAYS:
        if t == "reasoning" and not g.super_thinking:
            return False
        return True
    if t in ASSET:
        return g.assets
    if t in TELEMETRY_TRACE:
        return g.telemetry in ("trace", "full")
    if t in TELEMETRY_FULL:
        return g.telemetry == "full"
    if t == "craft":
        return g.craft
    if t == "tool":
        return g.tool
    if t in ("image", "voice", "audio", "video"):
        return getattr(g, t, True)
    if t == "source":
        return g.sources
    return True


# Event class → block type. Anything absent (IncrementalFlush and any future
# internal-only event) folds to nothing and never crosses the wire.
FOLD: dict[type, str] = {
    TextDelta: "text",
    ThinkingDelta: "reasoning",
    VoiceDelta: "voice",
    ImageAuthored: "image",
    AudioAuthored: "audio",
    VideoAuthored: "video",
    ToolExecution: "tool",
    ToolUseSummary: "tool",
    CraftRequest: "craft",
    CraftAuthored: "craft",
    SourceDiscovered: "source",
    NodeProgress: "progress",
    CharterCreated: "progress",
    InterruptibleStateChanged: "status",
    StrugglingDetected: "status",
    ActivityStateChanged: "status",
    ThreadLifecycle: "thread",
    ThreadCreated: "thread",
    ThreadArchived: "thread",
    ExecutionSummary: "summary",
    SubAgentStart: "sub_agent",
    SubAgentDone: "sub_agent",
    SubAgentCancelled: "sub_agent",
    SubAgentBackgrounded: "sub_agent",
    SubAgentProgress: "sub_agent",
    SubAgentIdle: "sub_agent",
    SubAgentSummary: "sub_agent",
    SubAgentForkMetrics: "sub_agent",
    RoutingDecision: "routing",
    ReplanRequested: "replan",
    AssetRequiredEvent: "asset",
    AssetActionEvent: "asset",
    PermissionRequestEvent: "asset",
    AssetStatusEvent: "asset",
    CompactionEvent: "compaction",
    ReactiveCompactionEvent: "compaction",
    MicrocompactEvent: "compaction",
    CompactionPtlRetryEvent: "compaction",
    CompactionPtlFailedEvent: "compaction",
    CompactionCircuitBreakerTripped: "compaction",
    CompactionCircuitBreakerProbeAttempt: "compaction",
    CompactionCircuitBreakerReset: "compaction",
    CompactionCircuitBreakerSkip: "compaction",
    TokenBudgetProgress: "budget",
    TokenBudgetContinuation: "budget",
    TokenBudgetCompleted: "budget",
    ModelFallback: "model",
    PersistentRetryWait: "model",
    MediaErrorRecovery: "model",
    PreToolUseHookExecuted: "hook",
    PostToolUseHookExecuted: "hook",
    StopHookExecuted: "hook",
    MemoryWriteEvent: "memory",
    FileRestorationEvent: "file",
    FileRestorationFailedEvent: "file",
    SubprocessSpawnedEvent: "subprocess",
    ReconnectSummaryEvent: "session",
}


def payload_of(event: Event) -> dict:
    """Best-effort dict of an event's fields, minus the ``thread_id`` routing key
    we relift into the envelope (so it is not duplicated in the payload)."""
    if is_dataclass(event):
        data = asdict(event)
    else:  # pragma: no cover - all events are dataclasses today
        data = {"type": type(event).__name__, "error": "serialization_failed"}
    data.pop("thread_id", None)
    return data


def thread_id_of(event: Event, scope_id: str) -> str:
    """The owning thread for an event, or "" when the event belongs to none.

    ``scope_id`` is kept in the signature (and used for block-id derivation via
    :meth:`BlockSerializer.turn_id`) but must NOT be used here. The global sink
    is built as ``BlockSerializer()``, whose scope_id defaults to the sentinel
    "_", and three folded event classes carry no thread_id at all
    (ReconnectSummaryEvent, ActivityStateChanged, SubprocessSpawnedEvent).
    Coercing those to "_" made ``fan_block`` take its thread branch and look for
    a stream bound to "_" - a value no thread_id_box ever holds - so the block
    passed its gate and was then dropped by the router with no log. Returning ""
    routes them by turn id like every other engine-origin block, which is what
    ``sse_frame``'s ``block.thread_id or None`` already assumes.
    """
    return getattr(event, "thread_id", None) or ""


class BlockSerializer:
    """Stateful fold of the internal event stream into v2 blocks, scoped to one
    ``/execute`` stream. Construct one per stream (or one module-global that
    reads the per-request correlation id); call :meth:`fold` per event. Returns
    ``None`` for internal-only events.
    """

    def __init__(self, scope_id: str = "_") -> None:
        self.scope_id = scope_id  # fallback turn scope when no request in context

    # -- id derivation -----------------------------------------------------

    def turn_id(self, event: Event) -> str:
        """Per-turn correlation for append-stream and per-turn-singleton ids.

        Prefer an event-carried turn id (ExecutionSummary has one); else the
        current request correlation id (server.py's ``rid``, set on
        ``request_id_var`` per /execute and inherited by the turn's task) so
        concurrent turns get distinct text/reasoning/singleton ids; else the
        serializer scope id."""
        event_tid = getattr(event, "turn_id", "")
        if event_tid:
            return event_tid
        try:
            from src.logging_config import request_id_var

            rid = request_id_var.get()
            if rid:
                return rid
        except Exception:
            pass
        return self.scope_id

    def block_id(self, btype: str, event: Event) -> str:
        """Derive a stable, instance-distinct id so concurrent same-type blocks
        never collide. See the id rule in architecture/sections/25_block_contract.md."""
        tid = self.turn_id(event)

        if btype in APPEND_TYPES:
            # Scoped to the owning THREAD as well as the turn. TextDelta and
            # ThinkingDelta carry no turn_id, so every segment of a fanned-out
            # turn used to fold to the same text:{rid} / reasoning:{rid}. The
            # blocks still carried distinct thread_ids and the sink routed them
            # correctly, so the wire looked right - but a client following the
            # documented append-by-id rule interleaved two threads' prose into
            # one block token by token, and voice:{rid} made the product's TTS
            # speak two threads as a single utterance. VoiceDelta already
            # carries a turn_id override so sub-agent narration gets its own
            # block; that separation just was not extended to sibling threads.
            owner = thread_id_of(event, self.scope_id)
            return f"{btype}:{tid}:{owner}" if owner else f"{btype}:{tid}"

        if btype == "source":
            return source_block_id(tid, getattr(event, "handle", "") or tid)

        if btype == "craft":
            # The coordinator's await_block_input mints this id and registers
            # the waiter under it, so Block.id == pending_inputs key == the id
            # the client echoes back in block_input. Fall back to a per-turn id
            # for craft-authoring blocks that carry no pre-minted id.
            return getattr(event, "block_id", "") or f"craft:{tid}"

        if btype in ("image", "audio", "video"):
            # voice_parser mints "{kind}:{thread}:{turn_key}:{n}" so several
            # media galleries in one turn never collide under replace-by-id.
            return getattr(event, "block_id", "") or f"{btype}:{tid}"

        if btype == "tool":
            if isinstance(event, ToolUseSummary):
                ids = getattr(event, "tool_use_ids", None)
                return f"tool:{ids[0]}" if ids else f"tool:{tid}"
            return f"tool:{getattr(event, 'tool_use_id', '') or tid}"

        if btype == "hook":
            tuid = getattr(event, "tool_use_id", "")
            if tuid:
                return f"hook:{tuid}"
            return f"hook:{tid}:{getattr(event, 'turn_number', 0)}"

        if btype == "sub_agent":
            return f"sub_agent:{getattr(event, 'agent_id', '') or tid}"

        if btype == "progress":
            node = getattr(event, "node_id", "") or getattr(event, "charter_id", "")
            return f"progress:{node}" if node else f"progress:{tid}"

        if btype == "thread":
            return f"thread:{getattr(event, 'thread_id', '') or tid}"

        if btype == "asset":
            kind = self.asset_kind(event)
            server = getattr(event, "server_name", "") or ""
            action = getattr(event, "action_name", "") or ""
            conn = getattr(event, "connection_id", None) or ""
            if kind == "status":
                return f"asset:status:{conn or server or tid}"
            if action:
                return f"asset:{kind}:{server}:{action}"
            return f"asset:{kind}:{server or tid}"

        if btype == "memory":
            return f"memory:{getattr(event, 'key', '') or tid}"

        if btype == "file":
            return f"file:{getattr(event, 'path', '') or tid}"

        if btype == "subprocess":
            return f"subprocess:{getattr(event, 'spawn_site', '') or tid}"

        if btype == "compaction":
            return f"compaction:{self.compaction_kind(event)}:{tid}"

        if btype == "model":
            return f"model:{self.model_kind(event)}:{tid}"

        if btype == "status":
            return f"status:{self.status_kind(event)}:{tid}"

        # per-turn singletons: summary, routing, replan, budget, session, done, error
        return f"{btype}:{tid}"

    # -- discriminators ----------------------------------------------------

    @staticmethod
    def asset_kind(event: Event) -> str:
        """The subtype the single `asset` block carries in payload.kind."""
        return {
            "AssetRequiredEvent": "required",
            "AssetActionEvent": "action",
            "PermissionRequestEvent": "permission_request",
            "AssetStatusEvent": "status",
        }.get(type(event).__name__, "status")

    @staticmethod
    def status_kind(event: Event) -> str:
        if isinstance(event, InterruptibleStateChanged):
            return "interruptible"
        if isinstance(event, StrugglingDetected):
            return "struggling"
        if isinstance(event, ActivityStateChanged):
            return "activity"
        return "activity"

    @staticmethod
    def model_kind(event: Event) -> str:
        if isinstance(event, ModelFallback):
            return "fallback"
        if isinstance(event, PersistentRetryWait):
            return "retry_wait"
        if isinstance(event, MediaErrorRecovery):
            return "media_recovery"
        return "fallback"

    @staticmethod
    def compaction_kind(event: Event) -> str:
        return {
            CompactionEvent: "compaction",
            ReactiveCompactionEvent: "reactive",
            MicrocompactEvent: "microcompact",
            CompactionPtlRetryEvent: "ptl_retry",
            CompactionPtlFailedEvent: "ptl_failed",
            CompactionCircuitBreakerTripped: "cb_tripped",
            CompactionCircuitBreakerProbeAttempt: "cb_probe",
            CompactionCircuitBreakerReset: "cb_reset",
            CompactionCircuitBreakerSkip: "cb_skip",
        }.get(type(event), "compaction")

    @staticmethod
    def sub_agent_phase(event: Event) -> str:
        return {
            SubAgentStart: "start",
            SubAgentDone: "done",
            SubAgentCancelled: "cancelled",
            SubAgentBackgrounded: "backgrounded",
            SubAgentProgress: "progress",
            SubAgentIdle: "idle",
            SubAgentSummary: "summary",
            SubAgentForkMetrics: "fork_metrics",
        }.get(type(event), "progress")

    @staticmethod
    def thread_phase(event: Event) -> str:
        if isinstance(event, ThreadCreated):
            return "created"
        if isinstance(event, ThreadArchived):
            return "archived"
        return getattr(event, "status", "lifecycle")

    @staticmethod
    def hook_phase(event: Event) -> str:
        name = type(event).__name__
        if name.startswith("PreTool"):
            return "pre_tool"
        if name.startswith("PostTool"):
            return "post_tool"
        return "stop"

    @staticmethod
    def budget_phase(event: Event) -> str:
        if isinstance(event, TokenBudgetProgress):
            return "progress"
        if isinstance(event, TokenBudgetContinuation):
            return "continuation"
        return "completed"

    @staticmethod
    def type_of(event: Event) -> str | None:
        """Map an event class to its block type. None = internal-only."""
        return FOLD.get(type(event))

    # -- the fold ----------------------------------------------------------

    def fold(self, event: Event) -> Block | None:
        """Fold one internal event into a Block, or None if it does not cross
        to the block wire."""
        btype = self.type_of(event)
        if btype is None:
            return None
        payload = payload_of(event)
        self.stamp_discriminator(btype, event, payload)
        return Block(
            type=btype,
            payload=payload,
            thread_id=thread_id_of(event, self.scope_id),
            id=self.block_id(btype, event),
        )

    def stamp_discriminator(self, btype: str, event: Event, payload: dict) -> None:
        """Add the phase/kind discriminator the fold collapses many events onto,
        so consumers can tell folded subtypes apart from the payload alone."""
        if btype == "sub_agent":
            payload["phase"] = self.sub_agent_phase(event)
        elif btype == "asset":
            payload["kind"] = self.asset_kind(event)
        elif btype == "status":
            payload["kind"] = self.status_kind(event)
        elif btype == "model":
            payload["kind"] = self.model_kind(event)
        elif btype == "compaction":
            payload["kind"] = self.compaction_kind(event)
        elif btype == "thread":
            payload.setdefault("phase", self.thread_phase(event))
        elif btype == "hook":
            payload.setdefault("phase", self.hook_phase(event))
        elif btype == "file":
            payload.setdefault(
                "phase", "failed" if isinstance(event, FileRestorationFailedEvent) else "restored"
            )
        elif btype == "budget":
            payload.setdefault("phase", self.budget_phase(event))
        elif btype == "progress":
            payload.setdefault("phase", "charter" if isinstance(event, CharterCreated) else "node")
        elif btype == "source":
            # Reshape to display-only {kind, **data}: handle/turn_id scope the
            # block id (not shown to the product), and data is flattened up.
            payload.pop("handle", None)
            payload.pop("turn_id", None)
            data = payload.pop("data", {}) or {}
            payload.update(data)
        elif btype == "text":
            # Off-path (no citations): drop the empty key so the text payload is
            # byte-identical to today. Present only when the gate produced spans.
            if not payload.get("citations"):
                payload.pop("citations", None)
        elif btype == "voice":
            # turn_id only scopes the append-block id (sub-agent narration gets
            # its own block); the wire payload is exactly {"text": ...}.
            payload.pop("turn_id", None)
        elif btype in ("image", "audio", "video"):
            # block_id already IS the envelope id; the wire payload is exactly
            # {"title": ..., "items": [...]}.
            payload.pop("block_id", None)


class BlockBuffers:
    """Ring buffers of ``(seq, Block)`` keyed by a scope, one monotonic ``seq``
    per scope. The replay mechanics for both scopes live here so a fix to
    buffering, eviction or the cursor lands once instead of in two places.

    Keying the buffer and the counter together is what makes replay
    structurally leak-proof: ``replay(key, after)`` can only ever return blocks
    filed under that key, so ``?after=<seq>`` never crosses scopes.

    ``max_keys`` bounds the key space for scopes that do not clean up after
    themselves; leave it ``None`` when something else already prunes the keys.
    """

    def __init__(
        self, maxlen: int, max_keys: int | None = None, default_key: str = ""
    ) -> None:
        self.buffers: dict[str, deque] = {}
        self.seq: dict[str, int] = {}
        self.maxlen = maxlen
        self.max_keys = max_keys
        self.default_key = default_key

    def append(self, key: str, block: Block) -> int:
        """File a block under ``key`` and return its per-key seq (0 = not filed)."""
        key = key or self.default_key
        if not key:
            return 0
        seq = self.seq.get(key, 0) + 1
        self.seq[key] = seq
        if key not in self.buffers:
            if self.max_keys is not None:
                while len(self.buffers) >= self.max_keys:
                    oldest = next(iter(self.buffers))
                    self.buffers.pop(oldest, None)
                    self.seq.pop(oldest, None)
            self.buffers[key] = deque(maxlen=self.maxlen)
        self.buffers[key].append((seq, block))
        return seq

    def replay(self, key: str, after: int = 0) -> list[tuple[int, Block]]:
        """``(seq, Block)`` filed under ``key`` with ``seq > after``, and nothing
        else. Empty list for an unknown key."""
        return [(s, b) for (s, b) in self.buffers.get(key or self.default_key, ()) if s > after]

    def keys(self) -> list[str]:
        return list(self.buffers.keys())


class ThreadBlockBuffers(BlockBuffers):
    """Blocks scoped to a thread - what ``/execute/replay?thread_id=`` reads back.

    One instance lives on ``EngineState`` and the ``/execute`` sink appends
    every folded block. A block with no thread is filed under ``_`` rather than
    dropped, so nothing the sink sees goes unbuffered.

    The live ``/execute`` stream is pass-through (it forwards every block it
    sees); the per-thread *live* gate filter is applied at the sink. The
    guarantee here is on *replay*: a stream only ever replays its own thread.

    Unbounded in keys on purpose - the reaper prunes threads, so the key space
    is already bounded by the threads that exist.
    """

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__(maxlen, default_key="_")

    def append(self, block: Block) -> int:
        return super().append(block.thread_id, block)

    def threads(self) -> list[str]:
        return self.keys()


class TurnBlockBuffers(BlockBuffers):
    """Blocks scoped to a turn: the merged interleave one input produced.

    A turn can fan out across several threads, and it also carries blocks that
    belong to no thread at all (the dispatch report, the aggregate done).
    Per-thread buffers cannot reconstruct that: they hold N unrelated sequences
    and no home for the engine's own blocks. This keeps the turn as the caller
    saw it - every block, in delivery order, under one cursor - so a dropped
    connection resumes with a single ``?after=`` rather than N of them.

    Turn ids are minted per request and never reused, so nothing here would be
    collected on its own and ``max_turns`` evicts the oldest. The window only
    has to outlive a reconnect, which happens within seconds of a drop, and
    every retained turn pins its blocks' payloads in memory - so it is kept
    close to what recovery actually needs rather than as deep as it can afford.
    """

    def __init__(self, maxlen: int = 2000, max_turns: int = 32) -> None:
        super().__init__(maxlen, max_keys=max_turns)

    def turns(self) -> list[str]:
        return self.keys()


def make_aggregate_done_block(scope_id: str, outcomes: list[dict]) -> Block:
    """The terminal done for a turn that fanned out.

    Engine-origin: it reports on the turn as a whole, not on any one thread, so
    it carries no thread_id. ``segments`` names what happened to each piece of
    work the input became, which is the only place a caller can see that one
    piece failed while its siblings finished.
    """
    return Block(
        type="done",
        payload={"status": "completed", "segments": outcomes},
        thread_id="",
        id=f"done:{scope_id}",
    )


def make_done_block(
    scope_id: str, summary: dict, thread_id: str = "", terminal: bool = True
) -> Block:
    """Synthesize a ``done`` block from the last ExecutionSummary fields
    (status, num_turns, cost_usd, duration_ms).

    ``terminal`` says whether this done ends the turn. It is true for the only
    done of a single-thread turn and for the aggregate that closes a fanned-out
    one; it is false for a per-segment done, whose siblings are still running.
    A reader cannot infer this - it would have to know how many segments were
    dispatched - so the engine states it. Absent (older payloads) means
    terminal, which is what every done was before turns could fan out.
    """
    payload = dict(summary)
    if not terminal:
        payload["terminal"] = False
    # A per-segment done is keyed on its thread. All N+1 dones of a fanned-out
    # turn used to share id done:{scope_id}, so a client applying the contract's
    # replace-by-id reconciliation ended the turn with ONE done entry whose
    # thread_id had mutated from thread-a to thread-b to null. The numbers
    # survived in the aggregate's `segments` list, but the per-thread identity
    # server.py promises ("each thread gets its own done so a caller can tell
    # which piece of work finished") did not. The aggregate keeps done:{scope_id}.
    block_id = f"done:{scope_id}:{thread_id}" if (thread_id and not terminal) else f"done:{scope_id}"
    return Block(
        type="done",
        payload=payload,
        thread_id=thread_id or scope_id,
        id=block_id,
    )


def make_error_block(scope_id: str, error_payload: dict, thread_id: str = "") -> Block:
    """Synthesize an ``error`` block at the /execute boundary."""
    return Block(
        type="error",
        payload=dict(error_payload),
        thread_id=thread_id or scope_id,
        id=f"error:{scope_id}",
    )
