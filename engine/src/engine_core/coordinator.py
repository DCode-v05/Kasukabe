"""Coordinator - the single interface between the user and the engine.

Every input enters through the coordinator. Every output exits through
the coordinator. The coordinator is the central hub that:
1. Routes messages via LLM classification (ThreadRouter)
2. Creates unified threads with run() (Issue #4)
3. Owns the EventEmitter - all events flow through here
4. Owns the pause/resume path (await_block_input / resume_block)
5. Manages charter state (system prompt updates, not tool attachment)
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from src.engine_core.resume_state import ResumeStateManager
from src.engine_core.conversation_store import ConversationStore
from src.engine_core.dispatch_parser import normalize_whitespace, single_segment
from src.engine_core.schema import ThreadIndexEntry
from src.engine_core.events import (
    EventEmitter,
    CraftRequest,
    RoutingDecision,
    TextDelta,
    ThreadCreated,
    ThreadLifecycle,
)
from src.engine_core.thread import ExecutionThread
from src.memory.memory_factory import WorkingMemoryBackend
from src.memory.working_memory import EntryType
from src.herald import LLM
from src.herald.usage_tracker import UsageAccumulator, usage_tracker_var
from src.engine_core.schema import EXECUTABLE_VERDICTS, RequestOptions
from src.utility_directory.tool_runtime import ToolRegistry

if TYPE_CHECKING:
    from src.infra.sqlite_pool import SqlitePool
    from src.memory.consciousness import ConsciousnessManager
    from src.engine_core.schema import Dispatch, ThreadResult
    from src.engine_core.search import Search
    from src.utility_directory.utility import Utility


logger = logging.getLogger(__name__)

TRIVIAL_INPUT_MAX_CHARS = 11
GREETING_RE = re.compile(
    r"^(hi|hiya|hello|hey|yo|sup|howdy|thanks|thank you|thx|ty|ok|okay|cool|"
    r"nice|great|bye|goodbye|good\s+(morning|afternoon|evening|night))[\s.!?,]*$",
    re.IGNORECASE,
)


def is_trivial_input(message: str) -> bool:
    """True for inputs not worth a speculative embed (see gate note above)."""
    stripped = message.strip()
    return len(stripped) <= TRIVIAL_INPUT_MAX_CHARS or bool(GREETING_RE.match(stripped))


current_thread_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_thread_id",
    default="",
)

current_turn_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_turn_id",
    default="",
)

craft_gate_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "craft_gate",
    default=False,
)

craft_edit_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "craft_edit",
    default=None,
)

craft_wait_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "craft_wait",
    default=True,
)

citations_gate_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "citations_gate",
    default=False,
)

source_registry_var: contextvars.ContextVar = contextvars.ContextVar(
    "source_registry",
    default=None,
)

voice_gate_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "voice_gate",
    default=False,
)

voice_primary_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "voice_primary",
    default=False,
)

media_gates_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "media_gates",
    default=None,
)

media_registry_var: contextvars.ContextVar = contextvars.ContextVar(
    "media_registry",
    default=None,
)


SELECTION_KEYS: tuple[str, ...] = ("answers", "selected", "selections", "answer", "response")


def extract_selections(value) -> list:
    """Pull the user's chosen options out of a multichoice answer.

    ``InputUnion.value`` is typed ``dict | None``, so over HTTP a multichoice
    answer is ALWAYS a dict and never a list. Iterating it directly - which is
    what a bare ``value or []`` does - yields its KEYS, so an envelope like
    ``{"answer": ["Postgres"]}`` reported that the user had selected an option
    literally named "answer". The reply string was well-formed, no branch was
    skipped, and the suspended turn reasoned over a fabricated choice.

    So: unwrap a known envelope key first, and never fall through to iterating
    an arbitrary object. An unrecognised shape yields no selections, which the
    model reads as "nothing was chosen" - wrong, but visibly empty rather than
    invented.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        for key in SELECTION_KEYS:
            if key in value:
                return extract_selections(value[key])
        return []
    return [value]


class Coordinator:
    """Central hub - routes, creates threads, streams events, owns pause/resume."""

    def __init__(
        self,
        adapter: LLM,
        emitter: EventEmitter,
        memory: WorkingMemoryBackend,
        tools: ToolRegistry | None = None,
        db: "SqlitePool | None" = None,
        settings: Any | None = None,
        consciousness_mgr: "ConsciousnessManager | None" = None,
        ask_user_callback: Any | None = None,
        search: "Search | None" = None,
        utility: "Utility | None" = None,
        skill_catalog: Any | None = None,
        data_dir: str = "",
    ) -> None:
        self.adapter = adapter
        self.settings = settings
        self.emitter = emitter
        self.memory = memory
        self.tools = tools
        self.db = db
        self.consciousness_mgr = consciousness_mgr
        self.ask_user_callback = ask_user_callback
        self.search = search
        self.utility = utility
        self.skill_catalog = skill_catalog
        self.data_dir = data_dir
        self.signal_store = None
        if db is not None:
            try:
                from src.learning_centre.signals import SignalStore

                self.signal_store = SignalStore(db)
            except Exception:
                pass
        self.resume_state_mgr = ResumeStateManager(db)
        self.conversation_store = ConversationStore(db) if db is not None else None
        self.router: Any = None
        self.thread_index: dict[str, ThreadIndexEntry] = {}
        self.active_threads: dict[str, ExecutionThread] = {}
        self.threads: dict[str, ExecutionThread] = {}
        self.background_tasks: set[asyncio.Task] = set()
        self.task_timeout_seconds: float = 600.0
        self.pending_inputs: dict[str, tuple[asyncio.Future, str, str]] = {}

    def set_router(self, adapter: Any, alternative: Any = None) -> None:
        """No-op: ThreadRouter was removed for the Kasukabe build.

        Every dispatch takes build_dispatch's explicit-thread_id bypass, so
        ``self.router`` stays None and triage never runs. Kept as a method so
        bootstrap's wiring step does not need to know it is gone.
        """
        return None


    async def handle_input(
        self,
        message: str,
        media: list[dict] | None = None,
        super_thinking: bool = False,
        thread_id: str | None = None,
        options: "RequestOptions | None" = None,
        thread_id_box: list[str] | None = None,
        craft: bool = False,
        craft_wait: bool = True,
        citations: bool = False,
        voice: bool = False,
        voice_primary: bool = False,
        image: bool = False,
        audio: bool = False,
        video: bool = False,
        direct: bool = False,
    ) -> str | ThreadResult:
        """Main entry point - receives user message, routes to thread.

        Routing is always LLM-based via ThreadRouter. No similarity fallback,
        unless ``direct`` is set - see :meth:`build_dispatch`.

        ``thread_id_box`` is a one-element mutable cell the /execute stream
        hands in so the v2 sink can bind a fresh-input stream to its thread as
        soon as the target thread is resolved - before the first block is
        emitted - so live blocks route to the right stream.
        """
        logger.info("handle_input: thread_id=%s message_len=%d", thread_id, len(message))
        if super_thinking and (options is None or not options.super_thinking):
            options = options or RequestOptions()
            options.super_thinking = True
        prefetch_memory_task: asyncio.Task | None = None
        prefetch_usage: UsageAccumulator | None = None
        if self.search is not None and not is_trivial_input(message):
            prefetch_usage = UsageAccumulator()
            prefetch_memory_task = asyncio.create_task(
                self.prefetch_memory(message, usage=prefetch_usage)
            )

        dispatch = await self.build_dispatch(message, thread_id, direct=direct)
        await self.emit_dispatch(dispatch)

        plan = dispatch.executable

        status_segments = [s for s in dispatch.segments if s.verdict == "status"]
        status_task: asyncio.Task | None = None
        if status_segments:
            status_task = asyncio.create_task(self.answer_status(message))

        if not plan:
            if prefetch_memory_task is not None:
                prefetch_memory_task.cancel()
                if prefetch_usage is not None and prefetch_usage.embedding_tokens:
                    logger.info(
                        "prefetch cancelled after triage: %d embedding tokens "
                        "already spent (billed by citadel; no thread to attribute)",
                        prefetch_usage.embedding_tokens,
                    )
            if status_task is not None:
                return await status_task
            return await self.say(
                dispatch.reasoning or "Awaiting your decision on where this belongs."
            )

        turns = self.coalesce_segments(plan)

        if thread_id_box is not None:
            if not thread_id_box[0]:
                thread_id_box[0] = turns[0][0]
            for tid, _, _ in turns:
                if tid not in thread_id_box:
                    thread_id_box.append(tid)

        if len(turns) == 1 and status_task is None:
            target_thread_id, text, is_new_thread = turns[0]
            return await self.execute_segment(
                target_thread_id,
                text,
                is_new_thread=is_new_thread,
                media=media,
                options=options,
                craft=craft,
                craft_wait=craft_wait,
                citations=citations,
                voice=voice,
                voice_primary=voice_primary,
                image=image,
                audio=audio,
                video=video,
                prefetch_memory_task=prefetch_memory_task,
            )

        results = await asyncio.gather(
            *(
                asyncio.create_task(
                    self.execute_segment(
                        tid,
                        text,
                        is_new_thread=is_new,
                        media=media,
                        options=options,
                        craft=craft,
                        craft_wait=craft_wait,
                        citations=citations,
                        voice=voice,
                        voice_primary=voice_primary,
                        image=image,
                        audio=audio,
                        video=video,
                        prefetch_memory_task=prefetch_memory_task,
                    )
                )
                for tid, text, is_new in turns
            ),
            return_exceptions=True,
        )

        texts: list[str] = []
        failures = 0
        for (tid, _, _), outcome in zip(turns, results):
            if isinstance(outcome, BaseException):
                failures += 1
                logger.error("segment failed on thread %s: %s", tid, outcome)
                texts.append(f"[{tid}] failed: {outcome}")
            else:
                texts.append(outcome if isinstance(outcome, str) else str(outcome))
        if status_task is not None:
            texts.insert(0, await status_task)
        elif failures == len(turns):
            raise RuntimeError(f"all {failures} dispatched segments failed")
        return "\n\n".join(texts)

    async def build_dispatch(
        self, message: str, thread_id: str | None, direct: bool = False
    ) -> "Dispatch":
        """Decide what the input is before anything runs.

        Four structural bypasses skip triage entirely - they are addressing
        facts, not guesses about content:
          - an explicit thread_id (the caller already knows the thread),
          - ``direct`` (the caller asked not to be routed),
          - no threads yet (nothing to route against),
          - no router configured.

        ``direct`` is checked after thread_id because thread_id is the more
        specific instruction: it names the destination, while direct only says
        "do not triage".
        """
        if thread_id:
            return single_segment(
                message, verdict="continue", reasoning="explicit thread_id", target=thread_id
            )
        if direct:
            return single_segment(message, reasoning="direct")
        if not self.threads:
            return single_segment(message, reasoning="no threads yet")
        return single_segment(message, reasoning="router not configured")


    def enforce_pins(self, dispatch: "Dispatch") -> "Dispatch":
        """Downgrade any executable segment aimed at a PINNED thread.

        "Never route new input into a claimed thread" is asserted as absolute in
        three docstrings (server.py, schema.py, thread_router.py) and was
        enforced in exactly one place: the triage prompt. parse_dispatch is
        handed known_ids - every thread id, with no pin information - so it
        structurally cannot apply the rule even though its module docstring
        frames the model's output as untrusted and lists the validations it
        does apply. A non-compliant verdict produced a perfectly well-formed
        `continue` segment, and handle_input ran a full turn on a thread the
        pin/unpin layer believes only it can write to. Nothing logged, warned
        or failed; detecting the breach required the pinning caller to
        re-implement the check against the wire.

        `claimed` is the shape the model was supposed to produce, and its
        withhold-and-return machinery already exists, so the downgrade lands the
        segment where it belonged rather than inventing a new outcome. The
        explicit-thread_id bypass never reaches here: direct execution by
        thread_id is documented as unaffected by pinning.
        """
        for seg in dispatch.segments:
            if seg.verdict not in EXECUTABLE_VERDICTS or not seg.target:
                continue
            if not self.is_thread_pinned(seg.target):
                continue
            logger.warning(
                "dispatch: %s aimed at PINNED thread %s - downgrading to claimed",
                seg.verdict,
                seg.target,
            )
            if seg.target not in seg.related_ids:
                seg.related_ids = [seg.target, *seg.related_ids]
            seg.verdict = "claimed"
            seg.target = None
        return dispatch

    def coalesce_segments(self, segments: list) -> list[tuple[str, str, bool]]:
        """Turn segments into turns: (thread_id, text, is_new_thread).

        Segments aimed at the same existing thread are merged into ONE turn.
        The turn lock is per-thread, so two turns on one thread would serialize
        behind a 30s acquire and gain nothing; merging keeps the user's wording
        intact and costs one turn instead of two.

        New work is merged on the same principle. Extracts are verbatim cuts of
        one input, so two ``open`` segments carrying the same text - or one
        carrying text the other already covers - describe the same work however
        confidently triage split them. Left alone they would mint two threads
        for one intent, which is the duplicate-thread outcome the whole index
        design exists to avoid, arrived at from the other direction.
        """
        turns: list[tuple[str, str, bool]] = []
        by_target: dict[str, int] = {}
        opened: list[tuple[str, int]] = []

        for seg in segments:
            if seg.target:
                idx = by_target.get(seg.target)
                if idx is not None:
                    tid, text, is_new = turns[idx]
                    turns[idx] = (tid, f"{text}\n\n{seg.extract}", is_new)
                    continue
                by_target[seg.target] = len(turns)
                turns.append((seg.target, seg.extract, False))
                continue

            norm = normalize_whitespace(seg.extract)
            duplicate = next(
                (i for seen, i in opened if norm and (norm in seen or seen in norm)),
                None,
            )
            if duplicate is not None:
                tid, text, is_new = turns[duplicate]
                if norm not in normalize_whitespace(text):
                    turns[duplicate] = (tid, f"{text}\n\n{seg.extract}", is_new)
                continue
            opened.append((norm, len(turns)))
            turns.append((f"thread-{uuid.uuid4().hex[:8]}", seg.extract, True))

        return turns

    async def emit_dispatch(self, dispatch: "Dispatch") -> None:
        """Report the triage verdict before any work starts.

        Engine-origin block: it speaks for the engine, not for a thread, so it
        carries no thread_id.
        """
        await self.emitter.emit(
            RoutingDecision(
                thread_id="",
                segments=[asdict(s) for s in dispatch.segments],
                reasoning=dispatch.reasoning,
                active_thread_count=len(self.threads),
            )
        )

    async def report_failure(self, thread_id: str, start_time: float) -> None:
        """Announce a turn that died, however it died.

        A timeout and a raised exception are the same event to everyone
        downstream - the thread produced no result - so they report through one
        path rather than two that must be kept in step.
        """
        await self.emitter.emit(ThreadLifecycle(thread_id=thread_id, status="failed"))
        if self.db is not None:
            self.spawn_background(self.flush_signals(thread_id, start_time, status="failed"))

    async def say(self, text: str) -> str:
        """Speak to the user as the engine itself rather than as any thread.

        The coordinator reaches the user through exactly one channel - the
        emitter. Returning a string reaches nobody: handle_input's return value
        is an aggregate for the caller, not a wire frame, and every other
        verdict only appears to work because the thread it spawned emits on its
        own behalf. Engine-origin text carries no thread_id, so a reader knows
        the system is speaking and not one piece of work.
        """
        if text:
            await self.emitter.emit(TextDelta(thread_id="", text=text))
        return text

    async def answer_status(self, message: str) -> str:
        """Answer a question about the overall state of work.

        Written by the MAIN adapter, never by the triage model: this is the
        system speaking to the user, and the dispatch model is a light model
        chosen for classification. The answer never mentions threads, routing,
        or any internal faculty - one voice, from the outside.
        """
        from src.herald.types import Message as LLMMessage, SystemPromptBlock

        if self.adapter is None:
            return await self.say("No work is currently in flight.")

        lines = []
        for entry in self.thread_index.values():
            summary = (entry.summary or "").strip() or "(untitled)"
            state = (entry.current_state or "").strip()
            lines.append(f"- {summary}" + (f" - {state}" if state else ""))
        inventory = "\n".join(lines) if lines else "(nothing in flight)"

        system = [
            SystemPromptBlock(
                text=(
                    "You are answering a question about the work you currently have in "
                    "flight for this user. Speak as one assistant with one voice. Never "
                    "mention threads, routing, dispatch, coordinators, or any internal "
                    "machinery - the user has no concept of them and never should. "
                    "Summarize what is in flight in a sentence or a short list, then "
                    "offer to change or add to it. Be brief."
                )
            )
        ]
        prompt = f"Work in flight:\n{inventory}\n\nThe user asked: {message}"
        try:
            answer = await self.adapter.generate(
                [LLMMessage(role="user", content=prompt)], system=system
            )
        except Exception as e:
            logger.warning("status answer failed: %s", e)
            answer = (
                "Currently in flight:\n" + "\n".join(lines)
                if lines
                else "Nothing is in flight right now."
            )
        return await self.say(answer)

    async def execute_segment(
        self,
        target_thread_id: str,
        message: str,
        *,
        is_new_thread: bool,
        media: list[dict] | None = None,
        options: "RequestOptions | None" = None,
        craft: bool = False,
        craft_wait: bool = True,
        citations: bool = False,
        voice: bool = False,
        voice_primary: bool = False,
        image: bool = False,
        audio: bool = False,
        video: bool = False,
        prefetch_memory_task: asyncio.Task | None = None,
    ) -> str | ThreadResult:
        """Run one dispatched segment as a turn on its thread."""
        current_thread_id_var.set(target_thread_id)
        craft_gate_var.set(craft)
        craft_wait_var.set(craft_wait)
        citations_gate_var.set(citations)
        voice_gate_var.set(voice or voice_primary)
        voice_primary_var.set(voice_primary)
        media_gates_var.set({"image": image, "audio": audio, "video": video})
        source_registry_var.set(None)

        if is_new_thread:
            await self.emitter.emit(
                ThreadCreated(
                    thread_id=target_thread_id,
                    reason="first_message" if not self.threads else "new_task",
                )
            )

        try:
            await self.memory.write(target_thread_id, EntryType.MESSAGE_USER, message)
        except Exception as e:
            logger.debug("WM write (user input) failed: %s", e)

        thread = await self.get_or_create_thread(target_thread_id)

        registry = getattr(thread, "media_urls", None)
        media_registry_var.set(registry)
        if registry is not None:
            registry.add_from_text(message)
            for item in media or []:
                registry.add_from_text(str(item.get("content", "") or ""))

        ingestion_ids: list[str] = []
        ingestion_enabled = (options and options.content_ingestion is True) or (
            self.settings and self.settings.content_ingestion_enabled
        )
        thread.pinned = self.is_thread_pinned(target_thread_id)

        self.active_threads[target_thread_id] = thread
        start_time = time.monotonic()

        try:
            result = await asyncio.wait_for(
                thread.execute(
                    message,
                    thread_id=target_thread_id,
                    options=options,
                    ingestion_ids=ingestion_ids or None,
                    prefetch_memory_task=prefetch_memory_task,
                ),
                timeout=self.task_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Thread timed out after %.0fs for task %s",
                self.task_timeout_seconds,
                target_thread_id,
            )
            thread.cancel_requested = True
            await self.report_failure(target_thread_id, start_time)
            raise TimeoutError(
                f"Task {target_thread_id} timed out after {self.task_timeout_seconds}s"
            )
        except Exception as e:
            logger.error("Thread execution failed for task %s: %s", target_thread_id, e)
            await self.report_failure(target_thread_id, start_time)
            raise
        finally:
            self.active_threads.pop(target_thread_id, None)

        if self.db is not None:
            self.spawn_background(
                self.flush_signals(
                    target_thread_id,
                    start_time,
                )
            )

        result_str = result if isinstance(result, str) else str(result)
        await self.update_thread_index(target_thread_id, message, result_str)

        return result


    async def await_block_input(self, params):
        """Unified pause callback: emit a waiting block, suspend until resumed.

        Wired as the thread's ``ask_user_callback`` (bootstrap), so all three
        pause paths - the ask_user tool, permission prompts, and denial
        escalation - funnel through here. Mints the block_id, registers the
        waiting future in ``pending_inputs`` under it, emits a ``CraftRequest``
        carrying that block_id AND the real thread as ``thread_id`` (so the craft
        block routes correctly), then awaits the future with the 72h timeout.
        ``resume_block`` resolves it out-of-band - never touching the turn lock.
        """
        from src.sandbox.permissions import parse_ask_request, flatten_ask_reply
        from src.engine_core.thread import USER_INPUT_TIMEOUT_SECONDS

        question, choices, suggested = parse_ask_request(params)
        is_permission = bool(choices) or bool(suggested)

        fmt = "permission" if is_permission else "confirm"
        options: list[dict] = []
        if is_permission:
            options = [{"label": c, "value": c} for c in choices]
        elif isinstance(params, dict):
            fmt = (params.get("format") or "confirm").lower()
            for opt in params.get("options") or []:
                if isinstance(opt, dict):
                    label = str(opt.get("label", "") or opt.get("value", ""))
                    value = str(opt.get("value", "") or opt.get("label", ""))
                else:
                    label = value = str(opt)
                if label:
                    options.append({"label": label, "value": value})

        thread_id = current_thread_id_var.get() or ""
        block_id = f"craft:{thread_id or 'na'}:{time.monotonic_ns()}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending_inputs[block_id] = (future, fmt, thread_id)

        await self.emitter.emit(
            CraftRequest(
                thread_id=thread_id,
                question=question,
                gap_type=None,
                format="confirm" if is_permission else fmt,
                options=options,
                suggested_rule=suggested,
                block_id=block_id,
            )
        )
        logger.info(
            "Block input waiting: block_id=%s thread=%s format=%s question=%s",
            block_id,
            thread_id,
            fmt,
            question[:100],
        )
        try:
            if self.is_thread_pinned(thread_id):
                payload = await future
            else:
                payload = await asyncio.wait_for(future, timeout=USER_INPUT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if is_permission:
                return {"response": "deny", "feedback": "", "content_blocks": []}
            return "Error: input timed out after 72 hours"
        finally:
            self.pending_inputs.pop(block_id, None)
        return payload if is_permission else flatten_ask_reply(payload)

    @staticmethod
    def resolve_payload(fmt: str, value, text: str) -> dict:
        """Build the reply a suspended turn's future expects from a block_input.

        ``value`` is the structured answer the client sent (a craft action /
        user-input answer); ``text`` is free-form text alongside it. multichoice
        collapses a list of selections into the deterministic
        ``"selected: [...]; other: <text>"`` string the v1 wire used; every
        other format takes the single answer. ``feedback`` carries the free
        text so permission prompts keep their "approve with a note" behavior.
        """
        if fmt == "multichoice":
            selected = extract_selections(value)
            joined = ", ".join(str(s) for s in selected)
            response = f"selected: [{joined}]; other: {(text or '').strip()}"
        elif isinstance(value, dict):
            inner = value.get("response", value.get("answer", ""))
            if inner:
                response = str(inner)
            elif text:
                response = text
            else:
                response = json.dumps(value, default=str, sort_keys=True)
        elif value is not None:
            response = str(value)
        else:
            response = text or ""
        return {"response": response, "feedback": text or "", "content_blocks": []}

    @staticmethod
    def detect_craft_edit(value) -> dict | None:
        """A craft edit arrives as ``value = {edit, current{key, version, …}}``.

        Returns the normalized edit ({edit, current, key, version}) or None when
        the value is not an edit (a plain route:engine state snapshot, a string, …).
        """
        if not isinstance(value, dict):
            return None
        current = value.get("current")
        if "edit" not in value or not isinstance(current, dict):
            return None
        try:
            version = int(current.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        return {
            "edit": value.get("edit") or "",
            "current": current,
            "key": str(current.get("key", "")),
            "version": version,
        }

    @staticmethod
    def compose_edit_message(edit_text: str, current: dict) -> str:
        """Turn input for a craft edit: the edit instruction + the attached current
        craft. The engine re-authors the SAME key; the version is stamped for it."""
        instruction = (edit_text or "").strip() or "Apply the requested edit."
        current_json = json.dumps(current, indent=2, default=str, sort_keys=True)
        return (
            "Edit this craft and re-emit it with the SAME key (its version is "
            f"stamped for you). Apply this change: {instruction}\n\n"
            f"current craft:\n```json\n{current_json}\n```"
        )

    @staticmethod
    def compose_block_input_message(text: str, value) -> str:
        """Turn input for a fresh-turn block_input (route:engine / craft action).

        A dict ``value`` is the component's state snapshot - serialize it after the
        command text so the engine reasons over exactly what the component sent
        (impl §7). A string value (or none) falls back to plain text.
        """
        if isinstance(value, dict):
            snapshot = json.dumps(value, indent=2, default=str, sort_keys=True)
            command = (text or "").strip()
            section = f"component state:\n```json\n{snapshot}\n```"
            return f"{command}\n\n{section}" if command else section
        if isinstance(value, str) and value:
            return text or value
        return text or ""

    async def resume_block(
        self,
        thread_id: str,
        block_id: str,
        value=None,
        text: str = "",
        *,
        craft: bool = False,
        craft_wait: bool = True,
        citations: bool = False,
        voice: bool = False,
        voice_primary: bool = False,
        image: bool = False,
        audio: bool = False,
        video: bool = False,
    ):
        """Resolve a paused turn's waiting future, lock-free - or run fresh.

        (a) A turn is suspended on ``block_id`` → resolve its future DIRECTLY
            (never calls thread.execute(), never touches the held turn lock),
            exactly as the deleted v1 answer path did. The suspended /execute
            turn wakes, continues, and its continuation blocks route to the
            block_input stream by thread_id.
        (b) No live waiter (a route:engine action / craft edit on a completed
            thread) → start a fresh turn via handle_input (the lock is free). A
            dict ``value`` (the component's state snapshot) is carried into the
            turn so the engine reasons over what the component sent; a craft edit
            (``value={edit, current}``) re-authors the same key at version+1 with
            the craft gate forced on. ``craft`` threads the request's craft gate
            so a route:engine wake may (re-)author a craft.

        Returns ``(thread_id, resumed)``. ``resumed`` is True when a paused
        waiter was resolved - its OWN turn task then fans the continuation and
        terminal done, so the caller must not emit a done and must keep that
        stream registered. ``resumed`` is False when no waiter existed and a
        fresh turn was run here (the caller owns the terminal done as usual).
        """
        entry = self.pending_inputs.get(block_id)
        if entry is not None:
            future, fmt, owner_thread = entry
            if not future.done():
                future.set_result(self.resolve_payload(fmt, value, text))
                return owner_thread, True
            return owner_thread, True
        edit = self.detect_craft_edit(value)
        gate_kwargs = {
            "craft_wait": craft_wait,
            "citations": citations,
            "voice": voice,
            "voice_primary": voice_primary,
            "image": image,
            "audio": audio,
            "video": video,
        }
        if edit is not None:
            craft_edit_var.set({"key": edit["key"], "version": edit["version"]})
            message = self.compose_edit_message(edit["edit"] or text, edit["current"])
            result = await self.handle_input(
                message, thread_id=thread_id or None, craft=True, **gate_kwargs
            )
            return result, False
        message = self.compose_block_input_message(text, value)
        result = await self.handle_input(
            message, thread_id=thread_id or None, craft=craft, **gate_kwargs
        )
        return result, False


    async def prefetch_memory(
        self, message: str, usage: "UsageAccumulator | None" = None
    ) -> dict:
        """Speculative memory recall - embed once, fan out to episodic /
        knowledge / social. Returns a bundle the executing thread consumes
        instead of running its own serial recall. Always best-effort: any
        failure returns a partial bundle so the thread can fall back.

        ``usage`` fixes the accounting blind spot: this task runs BEFORE
        thread.execute() binds its per-turn UsageAccumulator, so without an
        explicit binding here the embed's tokens vanish from engine-side
        totals (citadel still bills them). The accumulator is bound to this
        task's context for the duration, its count rides the bundle as
        ``embedding_tokens`` for the executing thread to merge, and the
        coordinator logs it on the cancelled (no-thread) path.
        """
        bundle: dict = {
            "query_embedding": None,
            "episodes": [],
            "knowledge": [],
            "social": [],
            "embedding_tokens": 0,
        }
        if self.search is None:
            return bundle
        usage_token = None
        if usage is not None:
            usage_token = usage_tracker_var.set(usage)
        try:
            bundle["query_embedding"] = await self.search.embed(message)
        except Exception as e:
            logger.debug("Prefetch embed failed: %s", e)
        finally:
            if usage_token is not None:
                usage_tracker_var.reset(usage_token)
            if usage is not None:
                bundle["embedding_tokens"] = usage.embedding_tokens
        try:
            recall: list[Any] = await asyncio.gather(
                self.search.episodes(message, limit=10, query_embedding=bundle["query_embedding"]),
                self.search.knowledge(message, limit=10, query_embedding=bundle["query_embedding"]),
                self.search.social(message, limit=5, query_embedding=bundle["query_embedding"]),
                return_exceptions=True,
            )
            episodes, knowledge, social = recall[0], recall[1], recall[2]
            bundle["episodes"] = episodes if not isinstance(episodes, BaseException) else []
            bundle["knowledge"] = knowledge if not isinstance(knowledge, BaseException) else []
            bundle["social"] = social if not isinstance(social, BaseException) else []
        except Exception as e:
            logger.debug("Prefetch recall failed: %s", e)
        return bundle


    def is_thread_pinned(self, thread_id: str) -> bool:
        """A pinned thread is owned by the pin/unpin layer: exempt from the
        reaper and from waiting-period expiry."""
        entry = self.thread_index.get(thread_id)
        return bool(entry and entry.pinned)

    async def set_thread_pinned(self, thread_id: str, pinned: bool) -> bool:
        """Pin or unpin a thread. Returns False when the thread is unknown.

        Updates the in-memory index entry and persists the flag on the
        conversations row so it survives restart. Unpinning restores normal
        life: the idle clock (untouched by pinning) decides when the reaper
        collects it.
        """
        entry = self.thread_index.get(thread_id)
        if entry is None:
            return False
        previous = entry.pinned
        entry.pinned = pinned
        if self.conversation_store is not None:
            try:
                await self.conversation_store.set_pinned(thread_id, pinned)
            except Exception:
                entry.pinned = previous
                logger.error("pin persistence failed for %s - rolled back", thread_id)
                raise
        logger.info(
            "Thread %s %s by pin/unpin layer", thread_id, "pinned" if pinned else "unpinned"
        )
        return True

    async def update_thread_index(self, thread_id: str, message: str, result: str) -> None:
        """Update the task-index entry for a thread after execution.

        Mutates the in-memory dict (router uses it during the session)
        and persists summary / current_state / recent_turns to SQLite via
        ``save_thread_index`` so the router's worldview survives engine
        restart.  Pre-fix these fields existed only in memory and were
        lost on bootstrap, leaving the router unable to classify follow-up
        messages into restored threads.
        """
        from datetime import datetime, timezone

        entry = self.thread_index.get(thread_id)
        if entry is None:
            entry = ThreadIndexEntry(thread_id=thread_id)
            self.thread_index[thread_id] = entry

        if not entry.summary and message:
            entry.summary = message[:100]
        entry.current_state = (
            result[:150] if isinstance(result, str) else ""
        ) or entry.current_state
        entry.turn_count += 1
        entry.last_activity = datetime.now(timezone.utc).isoformat()

        entry.recent_turns.append(message[:100])
        if len(entry.recent_turns) > 3:
            entry.recent_turns = entry.recent_turns[-3:]

        if self.conversation_store is not None:
            await self.conversation_store.save_thread_index(
                thread_id,
                summary=entry.summary,
                current_state=entry.current_state,
                recent_turns=list(entry.recent_turns),
            )

    async def get_or_create_thread(self, thread_id: str) -> ExecutionThread:
        """Return an existing persistent thread or create + initiate a new one."""
        if thread_id in self.threads:
            return self.threads[thread_id]

        consciousness = None
        if self.consciousness_mgr:
            consciousness = await self.consciousness_mgr.get()

        thread = ExecutionThread(
            adapter=self.adapter,
            emitter=self.emitter,
            memory=self.memory,
            tools=self.tools,
            resume_state_mgr=self.resume_state_mgr,
            ask_user_callback=self.ask_user_callback,
            settings=self.settings,
            consciousness=consciousness,
            consciousness_mgr=self.consciousness_mgr,
            search=self.search,
            utility=self.utility,
            skill_catalog=self.skill_catalog,
            data_dir=self.data_dir,
            conversation_store=self.conversation_store,
            signal_store=self.signal_store,
        )
        await thread.initiate(thread_id=thread_id)
        self.threads[thread_id] = thread
        return thread

    def spawn_background(self, coro) -> asyncio.Task:
        """Launch a background task and track it for graceful shutdown."""
        from src.infra.bg_tasks import track_background

        return track_background(asyncio.create_task(coro), self.background_tasks)

    async def flush_signals(
        self,
        thread_id: str,
        start_time: float,
        status: str = "completed",
    ) -> None:
        """Fire-and-forget: flush WM execution data to learning_signals + trajectory_store."""
        if self.signal_store is not None:
            try:
                items = await self.memory.read(thread_id)
                await self.signal_store.flush_wm(thread_id, items, status=status)
            except Exception as e:
                logger.warning("Signal flush failed for task %s: %s", thread_id, e)

        try:
            from src.engine_core.trajectory import flush_wm_to_lcq

            duration_ms = int((time.monotonic() - start_time) * 1000)
            await flush_wm_to_lcq(
                memory=self.memory,
                thread_id=thread_id,
                db=self.db,
                duration_ms=duration_ms,
                status=status,
            )
        except Exception as e:
            logger.warning("Trajectory store flush failed for task %s: %s", thread_id, e)


    async def store_content(
        self,
        thread_id: str,
        title: str,
        content: str,
        source: str = "craft",
        metadata: dict | None = None,
    ) -> str:
        """Load large content into the task's content store (SQLite resume_state).

        Called by the pin/unpin layer before handle_input for large
        crafts/documents. Returns a content_id for reference.
        """
        content_id = f"content_{uuid.uuid4().hex[:8]}"

        await self.resume_state_mgr.store_content(
            thread_id=thread_id,
            content_id=content_id,
            title=title,
            content=content,
            source=source,
            metadata=metadata,
        )
        logger.info("Stored content %s for task %s (%d chars)", content_id, thread_id, len(content))

        return content_id


    def on(self, callback) -> None:
        """Register event consumer on the coordinator's emitter."""
        self.emitter.on(callback)

    async def replay(self, callback) -> None:
        """Replay buffered events to a late-joining consumer."""
        await self.emitter.replay(callback)


    async def shutdown(self, timeout_seconds: float = 30.0) -> None:
        """Graceful shutdown - cancel active threads, drain background tasks."""
        if self.active_threads:
            logger.info("Shutting down %d active threads", len(self.active_threads))
            for tid, thread in self.active_threads.items():
                thread.cancel_requested = True
            await asyncio.sleep(min(timeout_seconds, 5.0))
        self.active_threads.clear()
        self.threads.clear()

        if self.background_tasks:
            logger.info("Draining %d background tasks", len(self.background_tasks))
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()

        try:
            from src.engine_core.agent_storage import evict_all_stale

            swept = await asyncio.wait_for(
                asyncio.to_thread(evict_all_stale, force=True),
                timeout=min(timeout_seconds, 10.0),
            )
            removed = len(swept["metadata"]) + len(swept["sidechain"])
            if removed:
                logger.info("Shutdown evicted %d stale agent artifacts", removed)
        except asyncio.TimeoutError:
            logger.warning("Shutdown eviction sweep timed out - artifacts remain")
        except Exception:
            logger.debug("Shutdown eviction sweep failed", exc_info=True)
        logger.info("Coordinator shutdown complete")
