"""HTTP API server - exposes the full engine over the network.

Wraps EngineState (all subsystems) and provides endpoints for:
- Execution (streaming via SSE)
- User input (bidirectional question/answer via block_input)
- Memory (knowledge, episodes, social graph, soul)
- Health (subsystem status)

Usage:
    ENGINE_API_KEY=<key> uvicorn src.server:app
    Or: python -m src.server --api-key <key>
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from collections import deque
from pydantic import BaseModel, Field, model_validator

from src.api_models import (
    HealthDeepResponse,
    HealthResponse,
    InstallPendingKeyRequest,
    InstallPendingKeyResponse,
    LcTriggerResponse,
    MemoryExportResponse,
    MemoryImportResponse,
    MemorySearchResponse,
    OkResponse,
    PendingInputsResponse,
    PinThreadResponse,
    RavenEventsRequest,
    RavenEventsResponse,
    RavenQueryRequest,
    RavenQueryResponse,
    RavenRetentionRequest,
    RavenRetentionResponse,
    RavenTrackRequest,
    RavenTrackUntypedRequest,
    ReloadCatalogResponse,
    ReloadCoreSoulResponse,
    SkillCreateResponse,
    SkillDeleteResponse,
    SkillDetailResponse,
    SkillUpdateResponse,
    SkillsResponse,
    SoulFullResponse,
    SoulResponse,
    ThreadListResponse,
)
from src.auth import (
    persisted_key_hash_path,
    hash_key,
    get_admin_hash,
    get_auth_state,
    init_admin_state,
    init_auth_state,
    is_valid_key_hash,
    verify_admin_key,
    verify_key_against_state,
)
from src.security.unicode_sanitizer import UnicodeSanitizationMiddleware
from src.request_id import RequestIdMiddleware
from src.server_sanitizer import scrub_mapping, scrub_text
from src import key_rotation
from src.raven import api as raven_api
from src.bootstrap import BootReport, EngineState, bootstrap, shutdown
from src.config import Settings
from src.infra.graceful_shutdown import is_shutting_down, register_drain_callback
from src.errors import EngineError, ErrorCode, engine_error_from_status, error_from_exception
from src.logging_config import configure_logging, engine_id_var, request_id_var
from src.engine_core.events import (
    Event,
    ExecutionSummary,
    ReconnectSummaryEvent,
    ThreadLifecycle,
)
from src.engine_core.blocks import (
    Block,
    BlockSerializer,
    Gates,
    passes_gates,
    sse_frame,
    make_done_block,
    make_aggregate_done_block,
    make_error_block,
)
from src.engine_core.coordinator import current_turn_id_var

logger = logging.getLogger(__name__)


async def emit_reconnect_summary(summary, turn_id: str = "") -> None:
    """Build a ReconnectSummaryEvent and fan it onto the event bus.

    ``turn_id`` must be supplied by the caller. The event carries no thread_id,
    so ``fan_block`` routes it by ``current_turn_id_var`` - and this coroutine
    runs in its own task, created BEFORE ``run_and_finish`` sets that contextvar,
    so the context copy it inherits holds the default "". Without an explicit
    turn id the "while you were away" card is folded, gated, and then matched
    against no stream at all.

    Attempts an LLM recap via the fast/light tier (``state.light_adapter``);
    falls back to the deterministic recap on any error.  Never raises -
    cosmetic side effect of user activity recording.

    NOTE: we intentionally do not pass the reconnect-triggering message as
    "last user message" context - that field is meant for the pre-away
    message, which is not reliably available here.  The structured stats
    carry enough signal on their own.
    """
    try:
        recap: str | None = None
        source: Literal["llm", "deterministic"] = "deterministic"
        adapter = state.light_adapter if state else None
        if adapter is None and state is not None:
            adapter = state.adapter
        if adapter is not None:
            try:
                from src.services.away_summary import generate_away_summary

                llm_recap = await generate_away_summary(adapter, summary)
                if llm_recap:
                    recap = llm_recap
                    source = "llm"
            except Exception:
                logger.debug("away_summary generation failed", exc_info=True)

        if recap is None:
            recap = summary.format_recap()

        event = ReconnectSummaryEvent(
            previous_state=summary.previous_state.value,
            idle_seconds=summary.idle_seconds,
            tool_calls=summary.stats.tool_calls,
            errors=summary.stats.errors,
            user_active_seconds=summary.stats.user_active_seconds,
            engine_active_seconds=summary.stats.engine_active_seconds,
            natural_language=recap,
            source=source,
        )
        if state and state.emitter is not None:
            token = current_turn_id_var.set(turn_id) if turn_id else None
            try:
                await state.emitter.emit(event)
            finally:
                if token is not None:
                    current_turn_id_var.reset(token)
    except Exception:
        logger.debug("reconnect summary emit failed", exc_info=True)


# Keeps strong refs to the fire-and-forget reconnect-summary tasks so
# Python GC cannot collect them mid-flight (CPython best practice).
reconnect_summary_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class InputUnion(BaseModel):
    """The v2 /execute input - a tagged union on ``kind``.

    - ``kind="input"``  → fresh user input; ``text`` is the message.
    - ``kind="block_input"`` → responding to a waiting block (a craft
      answer or a craft action); ``block_id`` names the block, ``value`` is the
      structured answer, ``text`` is free-form text alongside it.
    """

    kind: Literal["input", "block_input"] = "input"
    text: str = Field(default="", max_length=32_768)
    block_id: str | None = Field(default=None, max_length=256)
    value: dict | None = None


class ExecuteRequest(BaseModel):
    """v2 request: a nested ``input`` union + top-level output gates.

    Output gates (``craft``, ``image``, ``voice``, ``audio``, ``video``,
    ``tool``, ``telemetry``, ``super_thinking``) decide which block types this
    stream receives; ``direct`` bypasses the router; ``max_turns`` and
    ``content_ingestion`` are per-turn knobs.
    """

    input: InputUnion
    thread_id: str | None = Field(
        default=None,
        max_length=256,
        description="Target thread. Required for block_input; for input it routes to a specific thread, else the router decides.",
    )
    media: list[dict] | None = Field(
        default=None,
        max_length=100,
        description="Documents/media to ingest (max 100). Each item: {type, title, content, mime_type, source, metadata}",
    )
    # ── output gates (top-level) ──
    craft: bool = Field(default=False, description="Gate - may the engine emit craft blocks.")
    craft_wait: bool = Field(
        default=True, description="Gate - may a craft wait for input (only with craft:true)."
    )
    assets: bool = Field(
        default=True,
        description="Gate for asset-domain blocks (the single asset block, any payload.kind). Default on.",
    )
    image: bool = Field(default=False, description="Gate — image blocks.")
    voice: bool = Field(default=False, description="Gate — voice blocks.")
    voice_primary: bool = Field(
        default=False,
        description="Ears-first mode: narration carries the full response (implies voice:true).",
    )
    audio: bool = Field(default=False, description="Gate — audio blocks.")
    video: bool = Field(default=False, description="Gate — video blocks.")
    tool: bool = Field(default=True, description="Gate — tool blocks.")
    telemetry: Literal["off", "trace", "full"] = Field(
        default="off", description="Telemetry verbosity: off / trace / full."
    )
    super_thinking: bool = Field(
        default=False, description="Extended thinking - emits reasoning blocks."
    )
    citations: bool = Field(
        default=False,
        description="Gate - sourcing/citations. Off: no registry, no handle labels, no markers (zero added tokens). On: source blocks + citation spans.",
    )
    direct: bool = Field(default=False, description="Bypass the router.")
    model: str | None = Field(
        default=None,
        max_length=256,
        description="Explicit model id to use for this request, bypassing the router.",
    )
    # ── per-turn knobs ──
    max_turns: int | None = Field(
        default=None, ge=1, le=500, description="Max agent loop turns for this request"
    )
    content_ingestion: bool | None = Field(
        default=None,
        description="Per-request content ingestion override. True enables chunking even if global flag is off.",
    )

    @model_validator(mode="after")
    def check_block_input(self):
        if self.input.kind == "block_input" and not self.input.block_id:
            raise ValueError("block_input requires a block_id")
        return self


class MemorySearchRequest(BaseModel):
    query: str = Field(..., max_length=4_096)
    limit: int = Field(default=20, ge=1, le=100)


class MemoryImportRequest(BaseModel):
    bundle: dict = Field(..., description="Memory export bundle JSON")


class FeedbackRequest(BaseModel):
    thread_id: str = Field(..., max_length=256)
    feedback_type: str = Field(..., max_length=50)
    correction_text: str | None = Field(default=None, max_length=8_192)





# ---------------------------------------------------------------------------
# Server state (module-level, set during lifespan)
# ---------------------------------------------------------------------------

state: EngineState | None = None
boot_report: BootReport | None = None


def engine_state() -> EngineState:
    """Return the live engine state, asserting it has been initialized.

    Route handlers only serve traffic after the lifespan startup sets ``state``,
    so inside a handler ``state`` is always non-None. This accessor encodes that
    invariant for the type checker and raises a clear error if ever violated.
    """
    if state is None:
        raise RuntimeError("Engine state not initialized")
    return state


# Wall-clock timestamp of when bootstrap() completed.  Used by the
# coordinator list_threads route to disambiguate restored-on-boot threads
# from threads created after the engine was up.
boot_completed_at: "datetime | None" = None


@dataclass
class StreamReg:
    """One open /execute SSE stream's routing record.

    ``thread_id_box`` is a mutable cell: ``[""]`` until the turn's threads are
    resolved, then every thread this turn touches, primary first. A turn that
    fans out binds several; slot 0 remains the primary. The sink delivers a
    block to this stream when the block's thread is one of them - or, for a
    block that belongs to no thread, when the block's turn is this stream's -
    and only when it ``passes_gates``.
    """

    queue: "asyncio.Queue"
    thread_id_box: list
    gates: Gates
    seq: int = 0
    # The turn this stream issued. Blocks that belong to no thread (the
    # dispatch report, the aggregate done) are routed by this instead.
    turn_id: str = ""


# Active SSE streams: task → its StreamReg. Drained on shutdown.
active_streams: dict[asyncio.Task, StreamReg] = {}
# Turns parked on a waiting block (awaiting a block_input). Their /execute SSE
# has ended but the turn task is alive and holds the turn lock on its future;
# this strong ref keeps GC from collecting it while it waits for resume_block.
parked_turn_tasks: set[asyncio.Task] = set()
# Marker a stream dequeues to flush a done.status="awaiting" and close (distinct
# from the None terminal sentinel). Carries the waiting block_id.
AWAIT_SENTINEL = "__await__"
# Marker enqueued right after the terminal done frame so the stream closes on a
# typed signal, not by byte-matching the serialized frame for '"type": "done"'
# (a tool/craft payload carrying that literal nested key would trip the grep and
# truncate the turn). Mirrors AWAIT_SENTINEL; carries the done block_id.
DONE_SENTINEL = "__done__"

# v2 block wire. One global sink (registered at boot) folds every internal event
# into a Block, assigns its per-thread seq, and fans it to the streams that own
# the block's thread and whose gates pass it. The buffer/seq live in one place
# so /execute/replay?after=<seq> is coherent and leak-proof per thread.
block_serializer = BlockSerializer()
# Last ExecutionSummary seen per thread → its terminal done payload.
last_summary_by_thread: dict[str, dict] = {}
# Threads whose turn ended badly. A segment that fails emits no ExecutionSummary
# - it emits a failed lifecycle instead - so without this the end-of-turn review
# would find nothing for it and default it to completed. The fan-out itself
# behaves correctly either way (siblings finish, the turn ends); the cost of not
# tracking this is that the turn then reports a failure as a success, which is
# the one outcome worse than the failure.
# Bounded: a turn whose stream is cancelled (client disconnect, shutdown drain)
# never reaches the review that would consume its entry, so an unbounded set
# would keep every such thread for the life of the process - and a much later
# turn on a recycled id could inherit a stale verdict. Oldest-out once full.
FAILED_THREAD_WINDOW = 512
failed_threads: dict[str, None] = {}


def record_thread_failure(thread_id: str) -> None:
    if not thread_id:
        return
    failed_threads.pop(thread_id, None)
    while len(failed_threads) >= FAILED_THREAD_WINDOW:
        failed_threads.pop(next(iter(failed_threads)), None)
    failed_threads[thread_id] = None


def segment_outcome(thread_id: str) -> dict:
    """What actually happened to one segment, for the end-of-turn review."""
    summary = last_summary_by_thread.pop(thread_id, None)
    if summary is not None:
        return summary
    if thread_id in failed_threads:
        failed_threads.pop(thread_id, None)
        return {"status": "failed"}
    return {"status": "completed"}


def render_prompt_as_text(payload: dict) -> str:
    """Flatten an engine prompt (ask_user/permission) payload to plain text - used
    when the craft gate is off so the question still reaches a clean text stream."""
    question = payload.get("question", "")
    options = payload.get("options") or []
    if not options:
        return question
    lines = [question, ""]
    for opt in options:
        label = opt.get("label") or opt.get("value") or ""
        if label:
            lines.append(f"  - {label}")
    return "\n".join(lines)


def should_await(block, gates) -> bool:
    """A craft halts the stream only when craft_wait (the per-stream master switch)
    AND the craft's own payload["wait"] are both set. Holds for a craft-off degraded
    prompt too - the question still pauses for an answer, keyed by the same id."""
    return block.type == "craft" and gates.craft_wait and bool(block.payload.get("wait", True))


def degrade_or_frame(block, reg, cursor):
    """The SSE frame to deliver ``block`` to a stream with ``reg``, or None to skip.

    ``cursor`` is the per-thread buffer index; ``reg.seq`` is the per-stream
    delivered-frame counter, bumped only for frames actually delivered (gated
    blocks must NOT increment it, or the SDK's gap check would false-fire).

    Craft gate off: an engine-raised prompt (ask_user/permission, which carries a
    "question") degrades to a plain TEXT frame with the SAME block id - the caller
    still sees it and can answer via block_input, but gets a clean text stream (no
    craft UI). Authored crafts never reach here with the gate off (the model is
    never taught the tag). Otherwise apply the normal per-type gate.
    """
    gates = reg.gates
    if block.type == "craft" and not gates.craft:
        question = block.payload.get("question")
        if not question:
            return None
        degraded = Block(
            type="text",
            payload={"text": render_prompt_as_text(block.payload)},
            thread_id=block.thread_id,
            id=block.id,
        )
        reg.seq += 1
        return sse_frame(degraded, reg.seq, cursor)
    if not passes_gates(block, gates):
        return None
    reg.seq += 1
    return sse_frame(block, reg.seq, cursor)


def fan_block(block) -> None:
    """Buffer a block (per-thread seq) and fan it to every owning, gated stream.

    The single delivery path for EVERY block - folded events AND the synthesized
    done/error/awaiting blocks. Routing is by thread_id: a stream receives a
    block only once its ``thread_id_box`` is bound to that block's thread. A
    ``craft`` block additionally enqueues AWAIT_SENTINEL so the owning stream
    closes with done.status="awaiting" while the turn stays parked.
    """
    if state is None:
        return
    cursor = state.block_buffers.append(block)
    turn = current_turn_id_var.get()
    state.turn_buffers.append(turn, block)
    for reg in list(active_streams.values()):
        if block.thread_id:
            # No filtering of the box first: this branch already established
            # the block names a thread, and an unbound slot holds "", which
            # can never equal a real id.
            if block.thread_id not in reg.thread_id_box:
                continue
        else:
            # Speaks for the engine, not a thread: route by turn instead.
            if not turn or reg.turn_id != turn:
                continue
        frame = degrade_or_frame(block, reg, cursor)
        if frame is None:
            continue
        try:
            reg.queue.put_nowait(frame)
            if should_await(block, reg.gates):
                reg.queue.put_nowait((AWAIT_SENTINEL, block.id, block.thread_id))
            elif (
                block.type == "done"
                and block.payload.get("status") != "awaiting"
                and block.payload.get("terminal", True)
            ):
                # Only the done that ends the turn closes the stream. A
                # per-segment done is marked non-terminal by the engine, so
                # siblings still running are not cut off.
                reg.queue.put_nowait((DONE_SENTINEL, block.id))
        except Exception:
            pass


async def block_fan_sink(event: Event) -> None:
    """Global event→block sink: fold, record summary, fan via fan_block."""
    if state is None:
        return
    block = block_serializer.fold(event)
    if block is None:
        return  # internal-only event (e.g. IncrementalFlush)
    if isinstance(event, ThreadLifecycle) and getattr(event, "status", "") == "failed":
        record_thread_failure(block.thread_id)
    if isinstance(event, ExecutionSummary):
        last_summary_by_thread[block.thread_id] = {
            "status": event.status,
            "num_turns": event.tool_calls_count,
            "cost_usd": event.estimated_cost_usd,
            "duration_ms": event.duration_ms,
            "quota": event.quota,
        }
    fan_block(block)


SHUTDOWN_DRAIN_TIMEOUT = 30  # seconds to wait for in-flight tasks


# ---------------------------------------------------------------------------
# Rate limiter (sliding window, 60 req/min)
# ---------------------------------------------------------------------------

RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW = 60.0  # seconds
request_timestamps: deque[float] = deque()


def check_rate_limit() -> None:
    """Reject if more than RATE_LIMIT_MAX requests in the last RATE_LIMIT_WINDOW seconds."""
    now = time.monotonic()
    while request_timestamps and request_timestamps[0] < now - RATE_LIMIT_WINDOW:
        request_timestamps.popleft()
    if len(request_timestamps) >= RATE_LIMIT_MAX:
        err = EngineError(code=ErrorCode.RATE_LIMITED)
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())
    request_timestamps.append(now)


# Token usage is NOT stored in the engine. The ExecutionSummary SSE event
# streams cost/token data to the product layer, which records it in its own
# PG and enforces hard plan limits before the next request reaches the engine.


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def validate_key(x_engine_key: str = Header(...)) -> str:
    """Validate the engine API key and check rate limit."""
    state = get_auth_state()
    if state is None:
        err = EngineError(code=ErrorCode.KEY_NOT_CONFIGURED)
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())
    if not verify_key_against_state(x_engine_key, state):
        err = EngineError(code=ErrorCode.INVALID_KEY)
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())
    check_rate_limit()
    return x_engine_key


def validate_admin(x_admin_key: str = Header(...)) -> str:
    """Validate the orchestrator's per-engine admin secret (x-admin-key).

    Mirrors validate_key but against the separate admin auth state: 503
    key_not_configured when no admin key was provisioned, 401 invalid_key on a
    mismatch. Raises EngineError directly - the EngineError handler renders it.
    """
    if get_admin_hash() is None:
        raise EngineError(code=ErrorCode.KEY_NOT_CONFIGURED, message="admin key not configured")
    if not verify_admin_key(x_admin_key):
        raise EngineError(code=ErrorCode.INVALID_KEY, message="invalid admin key")
    return x_admin_key


# ---------------------------------------------------------------------------
# Lifespan - boot engine on startup, shutdown on exit
# ---------------------------------------------------------------------------


async def drain_server() -> None:
    """Drain active SSE streams and resolve pending user-input futures.

    Registered with graceful_shutdown.register_drain_callback() so it runs
    for signal-triggered shutdowns (SIGHUP, etc.) outside Uvicorn's lifecycle.
    Also called directly from the FastAPI lifespan on normal Uvicorn exit.
    """
    logger.info("Draining %d active streams", len(active_streams))

    # Signal all active SSE queues to close (push sentinel)
    for task, reg in list(active_streams.items()):
        reg.queue.put_nowait(None)

    # Resolve any paused-turn futures so parked turns unblock, finish, and
    # release their turn locks before subsystems go down. Parked turns are not
    # in active_streams (their SSE already ended), so this is the only way to
    # wake them - without it a turn awaiting input would hold its lock to death.
    coord = getattr(state, "coordinator", None) if state else None
    if coord is not None and getattr(coord, "pending_inputs", None):
        for block_id, (fut, fmt, tid) in list(coord.pending_inputs.items()):
            if not fut.done():
                fut.set_result(
                    {
                        "response": "Error: Engine shutting down",
                        "feedback": "",
                        "content_blocks": [],
                    }
                )

    # Wait for in-flight execution tasks (active + parked) to finish
    drain_tasks = list(active_streams.keys()) + list(parked_turn_tasks)
    if drain_tasks:
        done, pending = await asyncio.wait(drain_tasks, timeout=SHUTDOWN_DRAIN_TIMEOUT)
        if pending:
            logger.warning(
                "Force-cancelling %d tasks after %ds drain timeout",
                len(pending),
                SHUTDOWN_DRAIN_TIMEOUT,
            )
            for t in pending:
                t.cancel()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global state, boot_report, boot_completed_at

    log_level = os.environ.get("ENGINE_LOG_LEVEL", "INFO")
    configure_logging(level=log_level)

    # The one real settings load: parse + FULL validation, aggregated. A
    # ConfigError here propagates out of the lifespan and uvicorn refuses
    # to start - a misconfigured engine never serves traffic.
    settings = Settings.load_validated()

    # Pin engine_id into the logging context for the entire process
    # lifetime when the orchestrator provided one. Read through Settings so
    # the .env-file fallback applies (the old direct os.environ read
    # silently ignored ENGINE_ID set in .env).
    if settings.engine_id:
        engine_id_var.set(settings.engine_id)

    # Engine key resolution is PERSISTED-FIRST: a hot rotation writes the new
    # hash to /data, so on restart we prefer it over the (now stale) env hash.
    # Falls back to ENGINE_KEY_HASH, then hash_key(ENGINE_API_KEY). The
    # persisted value is only preferred when it is a valid hash - a corrupt or
    # partial file is ignored so it can never brick boot.
    persisted_hex = ""
    key_hash_file = persisted_key_hash_path()
    try:
        with open(key_hash_file, "r", encoding="utf-8") as f:
            persisted_hex = f.read().strip()
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to read persisted engine key hash from %s", key_hash_file)

    key_hash_hex = ""
    if persisted_hex:
        if is_valid_key_hash(persisted_hex):
            key_hash_hex = persisted_hex
            logger.info("Engine key hash loaded from %s", key_hash_file)
        else:
            logger.warning(
                "Persisted engine key hash at %s is invalid - ignoring it and falling back to env",
                key_hash_file,
            )

    if not key_hash_hex:
        key_hash_hex = os.environ.get("ENGINE_KEY_HASH", "")
        if key_hash_hex:
            logger.info("Engine key hash loaded from env")
        else:
            env_key = os.environ.get("ENGINE_API_KEY", "")
            if env_key:
                key_hash_hex = hash_key(env_key)
                logger.info("Engine key loaded from ENGINE_API_KEY env var")
            else:
                logger.warning("No engine key configured - all requests will be rejected")

    # Guard before init: an invalid/empty hash would make bytes.fromhex raise
    # out of lifespan and crash boot. Skip init instead - requests then get a
    # clean key_not_configured (auth state is None) rather than a hard crash.
    if is_valid_key_hash(key_hash_hex):
        init_auth_state(key_hash_hex)
    elif key_hash_hex:
        logger.warning(
            "Resolved engine key hash is invalid - skipping auth init; all requests will be rejected"
        )

    # Admin key (x-admin-key): orchestrator-only rotation credential. Mirrors
    # the engine-key path - ENGINE_ADMIN_KEY_HASH (hex) else hash_key of the raw
    # ENGINE_ADMIN_KEY. Absent on pre-admin engines → admin surface 503s.
    admin_hash_hex = os.environ.get("ENGINE_ADMIN_KEY_HASH", "")
    if admin_hash_hex:
        logger.info("Engine admin key hash loaded from env")
    else:
        admin_key = os.environ.get("ENGINE_ADMIN_KEY", "")
        if admin_key:
            admin_hash_hex = hash_key(admin_key)
            logger.info("Engine admin key loaded from ENGINE_ADMIN_KEY env var")
        else:
            logger.warning("No engine admin key configured - admin rotation surface disabled")

    # Guard before init for the same reason as the engine key: an invalid hash
    # must not crash boot. Skip init → the admin surface 503s (key_not_configured).
    if is_valid_key_hash(admin_hash_hex):
        init_admin_state(admin_hash_hex)
    elif admin_hash_hex:
        logger.warning(
            "Resolved admin key hash is invalid - skipping admin init; admin surface will 503"
        )

    # v2: the coordinator owns the pause path (await_block_input). bootstrap
    # rebinds the thread callback to it; no server-level input callback exists.
    state, boot_report = await bootstrap(settings=settings)
    boot_completed_at = datetime.now(timezone.utc)

    # Register server drain as a callback so signal-triggered shutdowns
    # (SIGHUP, SIGTERM outside Uvicorn) also drain SSE queues + input futures.
    register_drain_callback(drain_server)

    # Register the single global block sink - the v2 wire's buffer/seq authority.
    if state is not None and state.emitter is not None:
        state.emitter.on(block_fan_sink)

    if not boot_report.success:
        logger.error("Engine boot failed: %s", boot_report.errors)

    logger.info(
        "Engine ready: success=%s duration=%dms",
        boot_report.success,
        boot_report.boot_duration_ms,
    )
    yield

    # Uvicorn-triggered shutdown: drain streams, then close subsystems.
    logger.info("Shutdown initiated (Uvicorn lifespan exit)")
    await drain_server()

    # Stop the catalog poll task before this pod exits. Best-effort.
    try:
        from src.herald import catalog_sync

        await catalog_sync.shutdown()
    except Exception:
        logger.debug("catalog_sync shutdown failed", exc_info=True)

    logger.info("Drain complete - shutting down subsystems")
    if state:
        await shutdown(state)


app = FastAPI(
    title="September Engine",
    description="HTTP API for the September Engine - the agentic AI runtime: execution, memory, skills, and health.",
    servers=[{"url": "/"}],
    lifespan=lifespan,
)

# CORS - origins from CORS_ORIGINS env var; no-op if empty
# Module-import-time read: CORS middleware must register when the app object
# is built, before the lifespan runs. Deliberately the parse-only load() -
# semantic validation (and its hard-fail) belongs to the lifespan's
# load_validated(); importing this module must never require a full config.
cors_origins = Settings.load().cors_origins
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(UnicodeSanitizationMiddleware)
# Added last → outermost user middleware: it assigns the request id before any
# downstream code runs (so every endpoint's logs carry it) and appends the
# x-request-id header on the way out (covering every handler-rendered response).
app.add_middleware(RequestIdMiddleware)


def render_engine_error(err: EngineError) -> JSONResponse:
    """The one place that turns an EngineError into its wire response:
    ``{"error": {...}}`` at the error's http_status. Every error path renders
    through here so the envelope can never diverge."""
    return JSONResponse(status_code=err.http_status, content=scrub_mapping(err.to_dict()))


@app.exception_handler(EngineError)
async def engine_error_handler(request: Request, exc: EngineError) -> JSONResponse:
    """A directly-raised EngineError is already the structured error."""
    return render_engine_error(exc)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Unify every HTTPException onto the one ``{"error": {...}}`` wire shape.

    Handlers that raise ``HTTPException(detail=EngineError(...).to_dict())`` are
    already structured - surfaced verbatim. Everything else (an unmatched-route
    404 raised by Starlette, or an ad-hoc ``HTTPException(detail="…")``) is
    mapped to a structured error by status code, so no engine response ever
    ships a bare ``{"detail": ...}`` - the contract errors.py promises."""
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=scrub_mapping(exc.detail))
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else None
    return render_engine_error(engine_error_from_status(exc.status_code, message))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Request-body validation failures surface in the unified error envelope
    (the retired /rpc dispatcher used to map these to a structured 400)."""
    details = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    err = EngineError(
        code=ErrorCode.INVALID_PARAMS,
        message="Request validation failed",
        details={"errors": details},
    )
    return render_engine_error(err)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Safety net - any unhandled exception becomes a structured error."""
    return render_engine_error(error_from_exception(exc))


def require_subsystem(name: str, subsystem: Any) -> None:
    """Raise structured 503 if a subsystem is not available."""
    if subsystem is None:
        err = EngineError(
            code=ErrorCode.SUBSYSTEM_UNAVAILABLE,
            message=f"{name} not available",
        )
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())


def handle_endpoint_error(exc: Exception, log_msg: str) -> JSONResponse:
    """Convert an exception to a structured JSON error response."""
    logger.exception(log_msg)
    return render_engine_error(error_from_exception(exc))


# ---------------------------------------------------------------------------
# Execution endpoints
# ---------------------------------------------------------------------------


@app.post("/execute", operation_id="execute", tags=["engine"])
async def execute(req: ExecuteRequest, x_engine_key: str = Header(...)):
    """Execute a query and stream events via SSE."""
    validate_key(x_engine_key)
    if is_shutting_down():
        err = EngineError(code=ErrorCode.ENGINE_NOT_OPERATIONAL, message="Engine is shutting down")
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())
    if not state or not state.is_operational:
        err = EngineError(code=ErrorCode.ENGINE_NOT_OPERATIONAL)
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())

    # Adopt the request id assigned by RequestIdMiddleware so this turn's block
    # ids, log lines, and the x-request-id response header all share one value.
    # Fall back to generating one if the middleware didn't run (e.g. a direct
    # call in a test).
    rid = request_id_var.get()
    if not rid:
        rid = uuid.uuid4().hex[:12]
        request_id_var.set(rid)
    inp = req.input
    logger.info(
        "Execute start: kind=%s direct=%s thread_id=%s",
        inp.kind,
        req.direct,
        req.thread_id,
    )

    # Record user activity. If the user is rejoining after away/disconnected,
    # a ReconnectSummary comes back and we fan a ReconnectSummaryEvent onto the
    # event bus so frontends can render the "while you were away" card.
    reconnect_summary = None
    if state and state.activity_manager:
        reconnect_summary = state.activity_manager.record_user_activity()

    # This stream's record. The thread box starts unbound for fresh input (the
    # coordinator binds it as soon as the thread resolves) and is pre-bound for
    # block_input (the thread is named in the request). The sink routes blocks
    # to this stream by the bound thread + the gates parsed below.
    queue: asyncio.Queue = asyncio.Queue()
    thread_id_box = [req.thread_id or ""]
    # One id for everything this input produces, across however many threads it
    # turns out to touch. It is what a dropped connection resumes by.
    turn_id = f"turn-{rid}"
    gates = Gates.from_request(req)

    if reconnect_summary is not None:
        reconnect_task = asyncio.create_task(
            emit_reconnect_summary(reconnect_summary, turn_id)
        )
        reconnect_summary_tasks.add(reconnect_task)
        reconnect_task.add_done_callback(reconnect_summary_tasks.discard)

    async def run_and_finish() -> None:
        # Every block this turn produces is attributed here, including the ones
        # that belong to no thread. Segment tasks copy this context at creation,
        # so a fanned-out turn stays one turn on the wire.
        current_turn_id_var.set(turn_id)
        thread_id = req.thread_id or rid
        emit_terminal = True
        try:
            from src.engine_core.schema import RequestOptions

            options = None
            if (
                req.max_turns is not None
                or req.super_thinking
                or req.content_ingestion is not None
                or req.model is not None
            ):
                options = RequestOptions(
                    max_turns=req.max_turns,
                    super_thinking=req.super_thinking,
                    content_ingestion=req.content_ingestion,
                    model=req.model,
                )
            # What the turn returned, when this request is the one that ran it.
            # Stays None for a resumed turn, whose own /execute owns the done.
            outcome: Any = None
            if inp.kind == "block_input":
                # Resolve a paused turn's future lock-free, or run a fresh turn
                # if there is no waiter. resume_block reports which happened.
                owner, resumed = await state.coordinator.resume_block(
                    req.thread_id or "",
                    inp.block_id or "",
                    inp.value,
                    inp.text,
                    craft=req.craft,
                    craft_wait=req.craft_wait,
                    citations=req.citations,
                    voice=req.voice,
                    voice_primary=req.voice_primary,
                    image=req.image,
                    audio=req.audio,
                    video=req.video,
                )
                if resumed:
                    # A paused turn was resolved: ITS own task (the original
                    # /execute) fans the continuation + terminal done through
                    # THIS stream, so we stay silent (no second done) AND keep
                    # this stream registered - the finally below skips the pop
                    # so the resumed turn can deliver to it; stream() pops it
                    # when that real done arrives (or the client disconnects).
                    if isinstance(owner, str) and owner:
                        thread_id = owner
                        thread_id_box[0] = owner
                    emit_terminal = False
                else:
                    # No waiter - resume_block ran a fresh turn right here, so
                    # this request owns the terminal done.
                    outcome = owner
            else:
                outcome = await state.coordinator.handle_input(
                    inp.text,
                    media=req.media,
                    thread_id=req.thread_id,
                    options=options,
                    thread_id_box=thread_id_box,
                    craft=req.craft,
                    craft_wait=req.craft_wait,
                    citations=req.citations,
                    voice=req.voice,
                    voice_primary=req.voice_primary,
                    image=req.image,
                    audio=req.audio,
                    video=req.video,
                    direct=req.direct,
                )
            if outcome is not None:
                # A str outcome is the assistant's ANSWER TEXT, never a thread
                # id - assigning it fans the terminal done under a "thread" no
                # stream has bound, so the DONE_SENTINEL never reaches the
                # queue and the SSE hangs until the client times out. The real
                # id is ThreadResult.thread_id, or the box the coordinator
                # bound when the thread resolved.
                thread_id = getattr(outcome, "thread_id", None) or thread_id_box[0] or thread_id
        except asyncio.CancelledError:
            logger.info("Execution cancelled (shutdown drain)")
        except Exception as e:
            logger.exception("Execution error")
            err = error_from_exception(e)
            err_payload = scrub_mapping(err.to_dict()["error"])
            fan_block(make_error_block(rid, err_payload, thread_id))
        finally:
            if emit_terminal:
                bound = [t for t in thread_id_box if t]
                if len(bound) > 1:
                    # The input fanned out. Each thread gets its own done so a
                    # caller can tell which piece of work finished and how, and
                    # the turn ends with one engine-origin done carrying every
                    # segment's outcome - the frame that closes the stream.
                    outcomes = []
                    for tid in bound:
                        summary = segment_outcome(tid)
                        fan_block(make_done_block(rid, summary, tid, terminal=False))
                        outcomes.append({"thread_id": tid, **summary})
                    fan_block(make_aggregate_done_block(rid, outcomes))
                else:
                    summary = segment_outcome(thread_id)
                    fan_block(make_done_block(rid, summary, thread_id))
                active_streams.pop(task, None)
            # When emit_terminal is False a paused turn was resumed: leave this
            # stream registered so that turn's task can fan its continuation +
            # terminal done here. stream()'s own finally pops it once the done
            # arrives (686) or the client disconnects - so it is not leaked.

    task = asyncio.create_task(run_and_finish())
    active_streams[task] = StreamReg(
        queue=queue, thread_id_box=thread_id_box, gates=gates, turn_id=turn_id
    )

    async def stream() -> AsyncGenerator[str, None]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                # Shutdown-drain sentinel (drain_server) → close cleanly.
                if item is None:
                    return
                # A waiting craft → close the SSE with done.status="awaiting"
                # but leave the turn task alive (parked on its future). The
                # client answers via a new /execute block_input.
                if isinstance(item, tuple) and item and item[0] == AWAIT_SENTINEL:
                    # The thread that raised the question, not slot 0: in a
                    # fanned-out turn a sibling can be the one asking, and an
                    # awaiting done under the wrong thread names a thread that
                    # never asked anything.
                    tid = item[2] or thread_id_box[0] or rid
                    done = make_done_block(rid, {"status": "awaiting", "block_id": item[1]}, tid)
                    cursor = state.block_buffers.append(done)
                    reg = active_streams.get(task)
                    if reg is not None:
                        reg.seq += 1
                        wire_seq = reg.seq
                    else:
                        wire_seq = cursor
                    yield sse_frame(done, wire_seq, cursor)
                    parked_turn_tasks.add(task)
                    task.add_done_callback(parked_turn_tasks.discard)
                    active_streams.pop(task, None)
                    return
                # The terminal done frame was delivered on the item just before
                # this typed marker → close. Structural, so a tool/craft payload
                # carrying the literal '"type": "done"' can't truncate the turn.
                if isinstance(item, tuple) and item and item[0] == DONE_SENTINEL:
                    return
                yield item
        finally:
            active_streams.pop(task, None)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/execute/replay", operation_id="executeReplay", tags=["engine"])
async def replay_events(
    x_engine_key: str = Header(...),
    thread_id: str = "",
    turn_id: str = "",
    after: int = 0,
):
    """Replay buffered blocks for a reconnecting client.

    SSE connections can drop (network blip, tab close). Execution continues and
    blocks are buffered, so a client reconnects here to catch up.

    Two scopes, one cursor each:

    ``turn_id`` replays everything one input produced, in the order it was
    delivered - every thread it fanned out to, plus the blocks that belong to
    no thread (the dispatch report, the aggregate done). This is what a client
    that lost a multiplexed stream needs: per-thread replay would hand it N
    unrelated sequences and no way to recover the dispatch.

    ``thread_id`` replays one thread's blocks and nothing else, which is what a
    consumer following a single conversation wants.

    Query params:
        turn_id: replay one turn's merged interleave.
        thread_id: replay one thread (ignored when turn_id is given).
        after: skip blocks with seq <= after (0 = replay everything buffered).
    """
    validate_key(x_engine_key)
    if not state:
        err = EngineError(code=ErrorCode.ENGINE_NOT_OPERATIONAL)
        raise HTTPException(status_code=err.http_status, detail=err.to_dict())

    if turn_id:
        buffered = state.turn_buffers.replay(turn_id, after)
    else:
        buffered = state.block_buffers.replay(thread_id, after)
    frames = [sse_frame(block, seq, seq) for seq, block in buffered]

    async def replay_stream() -> AsyncGenerator[str, None]:
        for item in frames:
            yield item
        yield f'event: replay_done\ndata: {{"replayed": {len(frames)}}}\n\n'

    return StreamingResponse(
        replay_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Coordinator endpoints - thread inventory + claiming (SDK face)
# ---------------------------------------------------------------------------


@app.get(
    "/coordinator/threads",
    response_model=ThreadListResponse,
    operation_id="coordinatorListThreads",
    tags=["coordinator"],
)
async def list_threads(x_engine_key: str = Header(...)):
    """Enumerate every persistent conversation thread the coordinator knows about.

    Each entry carries the routing-relevant summary fields plus
    ``restored_on_boot`` - True when the thread was rehydrated from
    SQLite during ``bootstrap()`` Step 21b, False when it was created
    after the current process came up.

    Returns ``engine_not_operational`` if the coordinator never
    initialised; otherwise returns ``{boot_time, count, threads: [...]}``.
    """
    validate_key(x_engine_key)
    if state is None or state.coordinator is None:
        raise EngineError(
            code=ErrorCode.ENGINE_NOT_OPERATIONAL, message="coordinator not initialised"
        )
    boot_iso = boot_completed_at.isoformat() if boot_completed_at else ""
    restored_ids = state.restored_thread_ids
    threads: list[dict] = []
    for tid, entry in state.coordinator.thread_index.items():
        threads.append(
            {
                "thread_id": tid,
                "turn_count": entry.turn_count,
                "last_activity": entry.last_activity,
                "summary": entry.summary,
                "current_state": entry.current_state,
                "restored_on_boot": tid in restored_ids,
                "pinned": entry.pinned,
            }
        )
    threads.sort(key=lambda t: t["last_activity"], reverse=True)
    return {
        "boot_time": boot_iso,
        "count": len(threads),
        "threads": threads,
    }


async def set_thread_pinned_or_404(thread_id: str, pinned: bool) -> dict:
    """Shared body of the pin/unpin routes."""
    if state is None or state.coordinator is None:
        raise EngineError(
            code=ErrorCode.ENGINE_NOT_OPERATIONAL, message="coordinator not initialised"
        )
    known = await state.coordinator.set_thread_pinned(thread_id, pinned)
    if not known:
        raise EngineError(
            code=ErrorCode.NOT_FOUND, message=f"unknown thread: {thread_id}"
        )
    return {"ok": True, "thread_id": thread_id, "pinned": pinned}


@app.post(
    "/coordinator/threads/{thread_id}/pin",
    response_model=PinThreadResponse,
    operation_id="coordinatorPinThread",
    tags=["coordinator"],
)
async def pin_thread(thread_id: str, x_engine_key: str = Header(...)):
    """Pin a thread: the product layer takes ownership. A pinned thread is
    exempt from the conversation reaper and from waiting-period expiry, and
    the router never files new input into it. Direct execution by thread_id
    is unaffected."""
    validate_key(x_engine_key)
    return await set_thread_pinned_or_404(thread_id, True)


@app.post(
    "/coordinator/threads/{thread_id}/unpin",
    response_model=PinThreadResponse,
    operation_id="coordinatorUnpinThread",
    tags=["coordinator"],
)
async def unpin_thread(thread_id: str, x_engine_key: str = Header(...)):
    """Unpin a thread: it re-enters normal life - routable again, and the
    reaper applies from its last real activity (pinning never reset that
    clock)."""
    validate_key(x_engine_key)
    return await set_thread_pinned_or_404(thread_id, False)


# ---------------------------------------------------------------------------
# Raven endpoints - typed telemetry surface (SDK face)
# ---------------------------------------------------------------------------


@app.post("/raven/track", response_model=OkResponse, operation_id="ravenTrack", tags=["raven"])
async def raven_track(body: RavenTrackRequest, x_engine_key: str = Header(...)):
    """Track a typed telemetry event (Event dict with a ``kind`` key)."""
    validate_key(x_engine_key)
    await raven_api.track(body.event)
    return {"ok": True}


@app.post(
    "/raven/track_untyped",
    response_model=OkResponse,
    operation_id="ravenTrackUntyped",
    tags=["raven"],
)
async def raven_track_untyped(body: RavenTrackUntypedRequest, x_engine_key: str = Header(...)):
    """Track an ad-hoc named telemetry event with optional metadata."""
    validate_key(x_engine_key)
    await raven_api.track_untyped(body.name, body.metadata)
    return {"ok": True}


@app.post(
    "/raven/query", response_model=RavenQueryResponse, operation_id="ravenQuery", tags=["raven"]
)
async def raven_query(body: RavenQueryRequest, x_engine_key: str = Header(...)):
    """Run an aggregation query over the telemetry data."""
    validate_key(x_engine_key)
    try:
        return await raven_api.query(
            body.metric,
            window=body.window,
            group_by=body.group_by,
            filters=body.filters,
        )
    except ValueError as e:
        # Unknown window shorthand, bad metric, or a filter the chosen store
        # cannot honour → 4xx invalid_params, not 500.
        raise EngineError(code=ErrorCode.INVALID_PARAMS, message=str(e))


@app.post(
    "/raven/events", response_model=RavenEventsResponse, operation_id="ravenEvents", tags=["raven"]
)
async def raven_events(body: RavenEventsRequest, x_engine_key: str = Header(...)):
    """Read raw telemetry events newer than ``since``, optionally filtered by kind."""
    validate_key(x_engine_key)
    return await raven_api.events_page(since=body.since, kind=body.kind, limit=body.limit)


@app.post(
    "/raven/run_retention_now",
    response_model=RavenRetentionResponse,
    operation_id="ravenRunRetention",
    tags=["raven"],
)
async def raven_run_retention(body: RavenRetentionRequest, x_engine_key: str = Header(...)):
    """Manually trigger a retention pass - rolls up + prunes old telemetry."""
    validate_key(x_engine_key)
    return await raven_api.run_retention_now(
        raw_events_ttl_days=body.raw_events_ttl_days,
        counters_ttl_days=body.counters_ttl_days,
    )


# ---------------------------------------------------------------------------
# Admin - hot API key rotation (out of SDK)
# ---------------------------------------------------------------------------


@app.post(
    "/admin/install-pending-key",
    response_model=InstallPendingKeyResponse,
    operation_id="adminInstallPendingKey",
    tags=["admin"],
)
async def install_pending_key_endpoint(
    body: InstallPendingKeyRequest, x_admin_key: str = Header(...)
):
    """Install a pre-hashed new API key into the running engine (orchestrator-only).

    Authed with ``x-admin-key`` (the per-engine admin secret), NOT the engine
    key. ``install_pending_key`` raises ``EngineError`` (INVALID_PARAMS /
    ROTATION_IN_PROGRESS / INTERNAL_ERROR) on bad input, a pending rotation, or
    uninitialised auth state; surface it through the global error contract.
    """
    validate_admin(x_admin_key)
    # install_pending_key raises EngineError on bad input / pending rotation /
    # uninitialised auth; the EngineError handler renders it - no wrapping needed.
    return await key_rotation.install_pending_key(body.new_hash, body.overlap_seconds)


@app.get(
    "/inputs/pending",
    response_model=PendingInputsResponse,
    operation_id="inputsPending",
    tags=["engine"],
)
async def inputs_pending(x_engine_key: str = Header(...)):
    """List blocks currently awaiting an answer (read-only status).

    v2: answers arrive via ``POST /execute`` with an ``input.kind="block_input"``
    naming the ``block_id`` - not a separate endpoint. This endpoint just
    reports what is waiting, read from the coordinator's ``pending_inputs``.
    """
    validate_key(x_engine_key)
    coord = state.coordinator if state else None
    pending = []
    if coord is not None and getattr(coord, "pending_inputs", None):
        pending = [
            {"block_id": bid, "format": fmt, "thread_id": tid}
            for bid, (fut, fmt, tid) in coord.pending_inputs.items()
            if not fut.done()
        ]
    return {"pending": pending, "count": len(pending)}


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, operation_id="health", tags=["health"])
async def health():
    """Engine health - no auth required (used by orchestrator probes). Rate limited.

    Returns boot-time subsystem status only. Does NOT probe citadel
    reachability at request time - use /health/deep for that.
    """
    check_rate_limit()
    if boot_report is None:
        return JSONResponse(
            status_code=503,
            content={"status": "booting", "subsystems": {}},
        )
    # Body status: "healthy" requires BOTH a successful operational gate AND
    # an empty errors list.  Without the errors check we'd report healthy
    # while errors[] carries real failures - drift between the two arms of
    # the A1.5 contract.
    healthy = bool(boot_report.success and not boot_report.errors)
    body = {
        "status": "healthy" if healthy else "degraded",
        "subsystems": boot_report.subsystems,
        "warnings": boot_report.warnings,
        "errors": [scrub_text(e) for e in boot_report.errors] if boot_report.errors else [],
        "boot_duration_ms": boot_report.boot_duration_ms,
    }
    # HTTP status: 503 only when the engine is NOT operational
    # (coordinator missing - settings validation failed, LLM adapter
    # failed, etc.).  When the coordinator is alive but some
    # non-critical subsystem reported an error, return 200/degraded so
    # orchestrators keep routing traffic to the partially-up engine.
    # This matches the master A1 contract: "200 healthy / 200 degraded"
    # for an alive engine, "503" only for hard-not-ready.
    if not boot_report.success:
        return JSONResponse(status_code=503, content=body)
    return body


@app.get(
    "/health/deep", response_model=HealthDeepResponse, operation_id="healthDeep", tags=["health"]
)
async def health_deep():
    """Deep health probe - verifies citadel is reachable RIGHT NOW.

    Returns 503 if citadel /health doesn't respond 200. Used by the
    orchestrator's HealthMonitor to drive auto-restart when an engine
    pod is up but its upstream gateway is broken (network partition,
    citadel deploy mid-flight, DNS hiccup). The shallow /health
    endpoint only reflects boot-time state and would mask those.

    Probe is bounded to 2 seconds so the orchestrator's per-engine
    health budget isn't blown by a slow citadel.
    """
    check_rate_limit()
    settings = Settings.load()
    cdt_url = settings.cdt_url.rstrip("/") if settings.cdt_url else ""
    if not cdt_url:
        return JSONResponse(
            status_code=503,
            content={"status": "no-citadel-configured", "citadel_url": ""},
        )

    from september import APIStatusError, AsyncSeptember

    probe_start = time.monotonic()
    # Single attempt (max_retries=0): SDK retries would stretch a down
    # citadel to ~3x the 2s budget and blow the orchestrator's per-engine
    # health window. citadel.health() maps to GET {cdt_url}/health.
    sept = AsyncSeptember(
        citadel={"api_key": settings.cdt_key, "base_url": cdt_url},
        timeout=2.0,
        max_retries=0,
    )
    try:
        await sept.citadel.health()
        latency_ms = int((time.monotonic() - probe_start) * 1000)
        return {
            "status": "healthy",
            "citadel_url": cdt_url,
            "citadel_status": 200,
            "latency_ms": latency_ms,
        }
    except APIStatusError as e:
        # citadel answered but not 200 - a sick gateway, not a network failure
        return JSONResponse(
            status_code=503,
            content={
                "status": "citadel-unhealthy",
                "citadel_url": cdt_url,
                "citadel_status": e.status_code,
                "error": str(e)[:200] or type(e).__name__,
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "citadel-unreachable",
                "citadel_url": cdt_url,
                "error": str(e)[:200] or type(e).__name__,
            },
        )
    finally:
        await sept.aclose()


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/admin/reload-core-soul",
    response_model=ReloadCoreSoulResponse,
    operation_id="reloadCoreSoul",
    tags=["admin"],
)
async def reload_core_soul_endpoint(x_engine_key: str = Header(...)):
    """Hot-reload core_soul.json without restarting the service."""
    validate_key(x_engine_key)
    from src.memory.consciousness import reload_core_soul

    try:
        soul = reload_core_soul()
        # Invalidate all consciousness caches so next get() rebuilds with new soul
        if state and state.consciousness_mgr:
            state.consciousness_mgr.invalidate_all()
        return {"status": "reloaded", "version": str(soul.get("version", "unknown"))}
    except Exception:
        logger.exception("Failed to reload core soul")
        raise HTTPException(status_code=500, detail="Failed to reload core soul")



# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/memory/knowledge/search",
    response_model=MemorySearchResponse,
    operation_id="searchKnowledge",
    tags=["memory"],
)
async def search_knowledge(req: MemorySearchRequest, x_engine_key: str = Header(...)):
    """Search the knowledge store."""
    validate_key(x_engine_key)
    require_subsystem("Knowledge Store", state and state.knowledge_store)
    st = engine_state()
    try:
        results = await st.knowledge_store.search_knowledge(
            req.query,
            limit=req.limit,
        )
        return {"results": results}
    except Exception as e:
        return handle_endpoint_error(e, "Knowledge search failed")


@app.post(
    "/memory/episodes/search",
    response_model=MemorySearchResponse,
    operation_id="searchEpisodes",
    tags=["memory"],
)
async def search_episodes(req: MemorySearchRequest, x_engine_key: str = Header(...)):
    """Search episodic memory."""
    validate_key(x_engine_key)
    require_subsystem("Episodic Memory", state and state.episodic_memory)
    st = engine_state()
    try:
        results = await st.episodic_memory.search_episodes(
            req.query,
            limit=req.limit,
        )
        return {"results": results}
    except Exception as e:
        return handle_endpoint_error(e, "Episode search failed")


@app.post(
    "/memory/social/search",
    response_model=MemorySearchResponse,
    operation_id="searchSocial",
    tags=["memory"],
)
async def search_social(req: MemorySearchRequest, x_engine_key: str = Header(...)):
    """Search the social graph."""
    validate_key(x_engine_key)
    require_subsystem("Social Graph", state and state.social_graph)
    st = engine_state()
    try:
        results = await st.social_graph.search_social_graph(
            req.query,
            limit=req.limit,
        )
        return {"results": results}
    except Exception as e:
        return handle_endpoint_error(e, "Social graph search failed")


@app.get("/memory/soul", response_model=SoulResponse, operation_id="getSoul", tags=["memory"])
async def get_soul(x_engine_key: str = Header(...)):
    """Get the core soul and evolved traits."""
    validate_key(x_engine_key)
    require_subsystem("Soul", state and state.soul)
    st = engine_state()
    try:
        core = await st.soul.get_core_soul()
        evolved = await st.soul.get_evolved_soul()
        return {
            "core_soul": core.get("document", {}) if core else None,
            "evolved_traits": evolved or [],
        }
    except Exception as e:
        return handle_endpoint_error(e, "Soul retrieval failed")


@app.get(
    "/memory/soul/full",
    response_model=SoulFullResponse,
    operation_id="getSoulFull",
    tags=["memory"],
)
async def get_soul_full(x_engine_key: str = Header(...)):
    """Get all soul traits including unexpressed signals."""
    validate_key(x_engine_key)
    require_subsystem("Soul", state and state.soul)
    st = engine_state()
    try:
        core = await st.soul.get_core_soul()
        all_traits = await st.soul.get_evolved_soul_full()
        return {
            "core_soul": core.get("document", {}) if core else None,
            "all_traits": all_traits or [],
        }
    except Exception as e:
        return handle_endpoint_error(e, "Soul full retrieval failed")


# ---------------------------------------------------------------------------
# Memory portability endpoints (export / import)
# ---------------------------------------------------------------------------


@app.get(
    "/memory/export",
    response_model=MemoryExportResponse,
    operation_id="memoryExport",
    tags=["memory"],
)
async def memory_export(x_engine_key: str = Header(...)):
    """Export user's complete memory as a versioned JSON bundle."""
    validate_key(x_engine_key)
    require_subsystem("SQLite", state and state.db)
    st = engine_state()
    try:
        from src.memory.portability import export_memory

        bundle = await export_memory(st.db)
        return bundle
    except Exception as e:
        return handle_endpoint_error(e, "Memory export failed")


@app.post(
    "/memory/import",
    response_model=MemoryImportResponse,
    operation_id="memoryImport",
    tags=["memory"],
)
async def memory_import(req: MemoryImportRequest, x_engine_key: str = Header(...)):
    """Import a memory bundle into this user's memory."""
    validate_key(x_engine_key)
    require_subsystem("Episodic Memory", state and state.episodic_memory)
    require_subsystem("Knowledge Store", state and state.knowledge_store)
    require_subsystem("Social Graph", state and state.social_graph)
    require_subsystem("Soul", state and state.soul)
    st = engine_state()
    try:
        from src.memory.portability import import_memory

        result = await import_memory(
            bundle=req.bundle,
            episodic_memory=st.episodic_memory,
            knowledge_store=st.knowledge_store,
            social_graph=st.social_graph,
            soul=st.soul,
        )
        if st.consciousness_mgr:
            st.consciousness_mgr.invalidate()
        return {"ok": True, "imported": result}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        return handle_endpoint_error(e, "Memory import failed")


# ---------------------------------------------------------------------------
# Feedback + LC endpoints
# ---------------------------------------------------------------------------


@app.post("/feedback", response_model=OkResponse, operation_id="submitFeedback", tags=["engine"])
async def submit_feedback(req: FeedbackRequest, x_engine_key: str = Header(...)):
    """Submit user feedback for a completed task. Updates the LC queue entry."""
    validate_key(x_engine_key)
    require_subsystem("SQLite", state and state.db)
    st = engine_state()
    try:
        from src.engine_core.trajectory import record_feedback

        await record_feedback(
            st.db,
            req.thread_id,
            req.feedback_type,
            req.correction_text,
        )
        return {"ok": True}
    except Exception as e:
        return handle_endpoint_error(e, "Feedback submission failed")


@app.post(
    "/admin/lc/trigger",
    response_model=LcTriggerResponse,
    operation_id="triggerLcBatch",
    tags=["admin"],
)
async def trigger_lc_batch(x_engine_key: str = Header(...)):
    """Manually trigger an LC batch run. Returns processing results."""
    validate_key(x_engine_key)
    if state is None or state.lc_pipeline is None:
        raise HTTPException(status_code=503, detail="LC pipeline not available")
    try:
        result = await state.lc_pipeline.run_batch()
        return {
            "ok": True,
            "signals_processed": result.signals_processed,
            "episodes_created": result.episodes_created,
            "knowledge_written": result.knowledge_written,
            "social_nodes_created": result.social_nodes_created,
            "social_edges_written": result.social_edges_written,
            "errors": result.errors,
        }
    except Exception as e:
        return handle_endpoint_error(e, "LC batch trigger failed")


# ---------------------------------------------------------------------------
# Skill CRUD endpoints
# ---------------------------------------------------------------------------


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "custom"
    prompt: str = ""
    capabilities: list[str] = Field(default_factory=list)
    scripts: dict[str, str] = Field(default_factory=dict)
    references: dict[str, str] = Field(default_factory=dict)
    input_signature: dict = Field(default_factory=dict)
    output_signature: dict = Field(default_factory=dict)


class SkillUpdateRequest(BaseModel):
    description: str | None = None
    category: str | None = None
    prompt: str | None = None
    scripts: dict[str, str] | None = None


@app.get("/skills", response_model=SkillsResponse, operation_id="listSkills", tags=["skills"])
async def list_skills(x_engine_key: str = Header(...)):
    """List all available skills with source labels."""
    validate_key(x_engine_key)
    if state is None or state.catalog is None:
        raise HTTPException(status_code=503, detail="Catalog not loaded")
    skills = []
    for name, entry in state.catalog.skills.items():
        skills.append(
            {
                "name": name,
                "source": entry.source,
                "version": entry.version,
                "description": entry.data.get("description", ""),
                "category": entry.data.get("category", ""),
                "has_prompt": entry.data.get("has_prompt", False),
                "has_scripts": entry.data.get("has_scripts", False),
            }
        )
    return {"skills": skills}


@app.get(
    "/skills/{skill_name}",
    response_model=SkillDetailResponse,
    operation_id="getSkill",
    tags=["skills"],
)
async def get_skill(skill_name: str, x_engine_key: str = Header(...)):
    """Get skill metadata and content."""
    validate_key(x_engine_key)
    if state is None or state.catalog is None:
        raise HTTPException(status_code=503, detail="Catalog not loaded")
    skill = state.catalog.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    result = dict(skill)
    skill_dir_str = skill.get("_skill_dir", "")
    if skill_dir_str:
        from pathlib import Path

        skill_dir = Path(skill_dir_str)
        prompt_file = skill_dir / "prompt.md"
        if prompt_file.exists():
            result["prompt_content"] = prompt_file.read_text(encoding="utf-8")
        gotchas_file = skill_dir / "gotchas.md"
        if gotchas_file.exists():
            result["gotchas_content"] = gotchas_file.read_text(encoding="utf-8")
    return result


@app.post(
    "/skills/create",
    response_model=SkillCreateResponse,
    operation_id="createSkill",
    tags=["skills"],
)
async def create_skill(req: SkillCreateRequest, x_engine_key: str = Header(...)):
    """Create a user skill. Writes to /data/skills/{name}/."""
    validate_key(x_engine_key)
    if state is None or state.db is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    try:
        from pathlib import Path
        from src.catalog import create_user_skill

        data_dir = Path(state.settings.sqlite_db_path).parent
        result = create_user_skill(data_dir, req.model_dump())
        # Update database
        await state.db.execute(
            """INSERT INTO user_skills (id, name, source, status)
               VALUES (?, ?, 'user', 'active')
               ON CONFLICT (name) DO UPDATE SET status = 'active'""",
            (str(uuid.uuid4()), req.name),
        )
        await state.db.commit()
        # Reload catalog to include new skill
        if state.catalog:
            state.catalog.reload_user_skills(data_dir / "skills")
        return result
    except Exception as e:
        return handle_endpoint_error(e, "Skill creation failed")


@app.put(
    "/skills/{skill_name}",
    response_model=SkillUpdateResponse,
    operation_id="updateSkill",
    tags=["skills"],
)
async def update_skill(skill_name: str, req: SkillUpdateRequest, x_engine_key: str = Header(...)):
    """Update a user skill's content."""
    validate_key(x_engine_key)
    if state is None or state.db is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    # Only user/forked skills can be updated
    entry = state.catalog.skills.get(skill_name) if state.catalog else None
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    if entry.source not in ("user", "forked", "lc_created"):
        raise HTTPException(status_code=403, detail="Cannot update platform skills. Fork first.")
    try:
        from pathlib import Path
        from src.catalog import update_user_skill

        data_dir = Path(state.settings.sqlite_db_path).parent
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        result = update_user_skill(data_dir, skill_name, updates)
        if state.catalog:
            state.catalog.reload_user_skills(data_dir / "skills")
        return result
    except Exception as e:
        return handle_endpoint_error(e, "Skill update failed")


@app.delete(
    "/skills/{skill_name}",
    response_model=SkillDeleteResponse,
    operation_id="deleteSkill",
    tags=["skills"],
)
async def delete_skill(skill_name: str, x_engine_key: str = Header(...)):
    """Delete a user skill."""
    validate_key(x_engine_key)
    if state is None or state.db is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    entry = state.catalog.skills.get(skill_name) if state.catalog else None
    if entry and entry.source not in ("user", "forked", "lc_created"):
        raise HTTPException(status_code=403, detail="Cannot delete platform skills.")
    try:
        from pathlib import Path
        from src.catalog import delete_user_skill

        data_dir = Path(state.settings.sqlite_db_path).parent
        result = delete_user_skill(data_dir, skill_name)
        # Clean up database
        await state.db.execute(
            "DELETE FROM user_skills WHERE name = ?",
            (skill_name,),
        )
        await state.db.commit()
        if state.catalog:
            state.catalog.reload_user_skills(data_dir / "skills")
        return result
    except Exception as e:
        return handle_endpoint_error(e, "Skill deletion failed")


@app.post(
    "/admin/reload-catalog",
    response_model=ReloadCatalogResponse,
    operation_id="reloadCatalog",
    tags=["admin"],
)
async def reload_catalog(x_engine_key: str = Header(...)):
    """Hot-reload catalog from disk."""
    validate_key(x_engine_key)
    if state is None:
        raise HTTPException(status_code=503, detail="Engine not ready")
    try:
        from pathlib import Path
        from src.catalog import load_catalog

        data_dir = str(Path(state.settings.sqlite_db_path).parent)
        state.catalog = load_catalog(
            state.settings.catalog_dir,
            user_data_dir=data_dir,
        )
        if state.embedding_service:
            await state.catalog.embed_all_descriptions(state.embedding_service)
        return {
            "ok": True,
            "skills": len(state.catalog.skills),
            "agents": len(state.catalog.agents),
        }
    except Exception as e:
        return handle_endpoint_error(e, "Catalog reload failed")
