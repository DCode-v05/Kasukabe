"""ExecutionThread — unified agentic runtime for query execution.

Persistent thread: created once per task, reused across messages.
All tools are registered at initiation before the agent loop starts.
Charter goals (when present) add mission context to the system prompt
but do NOT change available tools.

The thread owns:
- agent_loop() — core LLM loop (model → tool_call → execute → result → model)
- execute_tool() — tool dispatch via ToolRegistry
- Tool registration — engine tools + platform tools, all upfront

ToolDef/ToolRegistry are owned by utility_directory.tool_runtime.
The thread receives a ready-made ToolRegistry and dispatches calls through it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

if TYPE_CHECKING:
    from src.engine_core.resume_state import ResumeStateManager
    from src.herald.types import ToolCall as LLMToolCall, ToolResultBlock, SystemPromptBlock
    from src.engine_core.execution_trace import (
        ExecutionTrace,
        NodeTrace,
    )

from src.engine_core.events import (
    EventEmitter,
    TextDelta,
    SourceDiscovered,
    ThinkingDelta,
    ThreadLifecycle,
    ToolExecution,
    NodeProgress,
    GapType,
    ReplanRequested,
    ExecutionSummary,
    StrugglingDetected,
    MemoryWriteEvent,
    PreToolUseHookExecuted,
    PostToolUseHookExecuted,
    StopHookExecuted,
    ToolUseSummary,
    AssetRequiredEvent,
    AssetActionEvent,
    PermissionRequestEvent,
    AssetStatusEvent,
)
from src.engine_core.conversation_store import ConversationPersistenceError
from src.memory.memory_factory import WorkingMemoryBackend
from src.memory.working_memory import EntryType
from src.herald import LLM, StopReason
from src.herald.defaults import ModelRole, resolve_model
from src.engine_core.context_engineering import (
    cap_turn_tool_results,
    cap_search_results,
)
from src.engine_core.cancellation import CancellationToken, InterruptBehavior
from src.engine_core.interrupt_source import get_interrupt_source
from src.engine_core.loop_state import LoopState, StrugglingState
from src.engine_core.pre_tool_hooks import (
    AggregatedPreToolResult as AggregatedPreToolResult,
    PreToolUseHookInput,
    PreToolUseHookRegistry,
)
from src.engine_core.post_tool_hooks import (
    AggregatedPostToolResult as AggregatedPostToolResult,
    PostToolHookContext,
    PostToolUseHookRegistry,
)
from src.engine_core.hooks import HookEvent
from src.engine_core.stop_hooks import (
    AggregatedStopResult,
    StopHookInput,
    StopHookRegistry,
)
from src.engine_core.streaming_tool_executor import StreamingToolExecutor
from src.engine_core.tool_result_builder import ToolResultBuilder
from src.engine_core.tool_result_storage import cap_builder_size
from src.engine_core.schema import (
    Charter,
    topological_sort,
    NodeCheckpoint,
    RequestOptions,
    LoopExitReason,
)


from src.utility_directory.tool_runtime import (
    ToolDef,
    ToolRegistry,
    partition_tool_calls,
    ALWAYS_SAFE,
)

from src.sandbox.permissions import (
    AskUserParams,
    AskUserReply,
    flatten_ask_reply,
    parse_ask_request,
    ToolSource,
    PermissionMode,
    PermissionRule,
    DenyRule,
    DenialTracker,
    DenialLimitExceeded,
    check_permission,
    INTERNAL_TOOL_NAMES,
    NETWORK_TOOL_NAMES,
)
from src.sandbox.permission_store import PermissionStore, PermissionProvenance

logger = logging.getLogger(__name__)


AskUserCallback = Callable[[AskUserParams], Awaitable[AskUserReply]]


async def generate_tool_use_summary(
    tool_calls: list,
    tool_results: list,
    last_assistant_text: str,
    settings: Any,
    emitter: "EventEmitter",
    thread_id: str,
    turn_number: int,
    cancellation_token: "CancellationToken | None" = None,
) -> None:
    """Background coroutine: generate and emit a ToolUseSummary event.

    Runs concurrently with the next agent turn via ``asyncio.ensure_future``.
    All exceptions are caught — this is strictly fire-and-forget.
    """
    if cancellation_token and cancellation_token.cancelled:
        return

    try:
        from src.engine_core.tool_use_summary import ToolInfo, generate_tool_use_summary
        from src.herald import create_llm
        from src.herald.catalog import resolve_model_for_tier, ModelTier
        from src.herald.types import flatten_tool_result_content_to_text

        result_map: dict[str, str] = {
            res.tool_use_id: flatten_tool_result_content_to_text(res.content)
            for res in tool_results
        }

        tool_use_ids = [tc.id for tc in tool_calls]

        tools = [
            ToolInfo(
                name=tc.name,
                input=tc.input,
                output=result_map.get(tc.id, ""),
            )
            for tc in tool_calls
        ]

        try:
            light_model = resolve_model_for_tier(ModelTier.LIGHT, settings)
            adapter = create_llm(settings, light_model.id)
        except Exception:
            return

        is_cancelled = (lambda: cancellation_token.cancelled) if cancellation_token else None

        summary = await generate_tool_use_summary(
            tools=tools,
            adapter=adapter,
            last_assistant_text=last_assistant_text,
            is_cancelled=is_cancelled,
        )
        if summary:
            await emitter.emit(
                ToolUseSummary(
                    thread_id=thread_id,
                    summary=summary,
                    turn_number=turn_number,
                    tool_count=len(tools),
                    tool_use_ids=tool_use_ids,
                )
            )
    except Exception:
        pass


def format_hook_prevent_notice(agg: AggregatedStopResult) -> str:
    """Build the user-visible termination notice appended to
    ``state.full_text`` when a stop hook forces the loop to exit.

    Mirrors the pattern at other forced-exit sites (cancellation,
    budget-exhausted): a short bracketed line listing the hook's
    stop_reason strings so the user can see *why* the agent halted
    rather than just seeing the loop end.
    """
    reasons = [r for r in agg.stop_reasons if r]
    if reasons:
        return f"\n\n[Stopped by hook: {'; '.join(reasons)}]"
    return "\n\n[Stopped by hook]"


class EmittingMemory:
    """Proxy: wraps WorkingMemoryBackend and emits MemoryWriteEvent on every write.

    Every self.memory.write() in the thread now surfaces to the TUI inspector.
    Transparent — all other methods delegate to the inner backend.
    """

    def __init__(self, inner: WorkingMemoryBackend, emitter: EventEmitter) -> None:
        self.inner = inner
        self.emitter = emitter

    async def write(
        self, thread_id: str, type: EntryType, content: str, sub_agent_id: str = ""
    ) -> None:
        await self.inner.write(thread_id, type, content, sub_agent_id=sub_agent_id)
        try:
            await self.emitter.emit(
                MemoryWriteEvent(
                    thread_id=thread_id,
                    memory_type=type.value,
                    key=type.value,
                    value_preview=str(content)[:200],
                )
            )
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


USER_INPUT_TIMEOUT_SECONDS = 72 * 3600


def craft_history_placeholder(craft: Any) -> str:
    """Compact stand-in for an authored craft in the model-facing text history.

    The client renders the structured craft block; the engine keeps only this
    marker in working memory / the conversation store and in the assistant
    message the model re-reads next turn — so it knows it rendered a UI (and
    which one) without re-reading the verbose <craft> markup or persisting a
    history that diverges from the stripped text the client saw.
    """
    label = (getattr(craft, "title", "") or getattr(craft, "key", "") or "").strip()
    suffix = " · awaiting input" if getattr(craft, "wait", False) else ""
    if label:
        return f'[craft "{label}" rendered{suffix}]'
    return f"[craft rendered{suffix}]"


async def default_ask_user(params: AskUserParams) -> AskUserReply:
    """Default ask-user callback — reads from stdin with timeout.

    Dict params (permission prompt) return a dict; string params
    (legacy ask_user tool) return a string. stdin has no image-paste
    channel, so content_blocks on the dict path is always empty.
    """
    if isinstance(params, dict):
        question, choices, suggested = parse_ask_request(params)
        lines = [f"\n? {question}"]
        if choices:
            lines.append(f"  options: {' / '.join(choices)}")
        if suggested:
            lines.append(f"  suggested_rule: {suggested}")
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(input, "\n".join(lines) + "\n> "),
                timeout=USER_INPUT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return {"response": "deny", "feedback": "", "content_blocks": []}
        return {"response": answer.strip(), "feedback": "", "content_blocks": []}

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(input, f"\n? {params}\n> "),
            timeout=USER_INPUT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return "(No user response - input timeout)"


def tool_source(tool_name: str) -> str:
    """Classify a tool call's source from its name prefix."""
    if tool_name.startswith("ad:"):
        return "asset"
    if tool_name.startswith("skill:"):
        return "skill"
    return "platform"


MAX_AGENT_TURNS = 200

TOOL_SEARCH_MAX_RESULTS = 5


def score_tool_match(query: str, tool: Any) -> int:
    """Rank a deferred tool against a search query. 0 means no match.

    Weighted so a name hit outranks a description hit and an exact token beats a
    prefix: an unweighted substring test made "send" rank `send_email` and
    `resend_invoice_reminder_webhook` identically.
    """
    name = (getattr(tool, "action_name", "") or tool.name).lower()
    description = (tool.description or "").lower()
    tokens = [t for t in name.replace(":", "_").replace(".", "_").split("_") if t]

    score = 0
    if query == name:
        score += 12
    elif query in tokens:
        score += 10
    elif any(t.startswith(query) for t in tokens):
        score += 6
    elif query in name:
        score += 3
    if query in description:
        score += 2
    return score


COMPLEXITY_NUDGE_THRESHOLD = 5

PREFETCH_BOUNDED_WAIT_SECONDS = 0.1


def merge_prefetch_usage(bundle: "dict | None") -> None:
    """Merge the coordinator prefetch's embed tokens into the CURRENT
    per-turn tracker.

    The prefetch task ran before thread.execute() bound its accumulator, so
    its embed was recorded into a detached accumulator and shipped on the
    bundle as ``embedding_tokens``. Folding it in here keeps engine-side
    usage totals equal to what citadel bills for the turn. Safe to call at
    most once per bundle (both consume paths are mutually exclusive)."""
    if not bundle:
        return
    tokens = int(bundle.get("embedding_tokens") or 0)
    if tokens:
        from src.herald.usage_tracker import record_embedding_usage

        record_embedding_usage(tokens)


def consume_pending_recall(
    messages: list,
    pending_recall_task: "asyncio.Task | None",
    recall_consumed: bool,
    *,
    message_factory,
    registry=None,
) -> tuple[list, bool]:
    """Per-iteration recall consume helper used inside ``agent_loop``.

    Peeks ``pending_recall_task`` without blocking. When the task has settled
    and we have not yet consumed it for this turn, builds a recall reminder
    via :func:`build_recall_reminder` and appends a synthetic user-role
    message to ``messages`` (Claude-Code-style "attachment as user message"
    pattern).

    ``message_factory`` is a callable that takes a ``content`` string and
    returns the engine's ``Message`` dataclass. Passed in so this module
    does not import the herald types at module import time.

    Always returns ``(messages, recall_consumed)``. Never raises.
    """
    if pending_recall_task is None or recall_consumed or not pending_recall_task.done():
        return messages, recall_consumed
    try:
        bundle = pending_recall_task.result()
        merge_prefetch_usage(bundle)
        block = build_recall_reminder(bundle, registry=registry) if bundle else None
        if block:
            messages = [*messages, message_factory(block)]
    except Exception as e:
        logger.debug("Recall consume failed: %s", e)
    return messages, True


def build_recall_reminder(bundle: dict, registry=None) -> str | None:
    """Format a memory prefetch bundle as a ``<system-reminder>`` string.

    Returns None when the bundle would surface nothing — keeps the agent
    loop's per-iteration consume path silent on misses instead of injecting
    empty reminders.

    Used in two places:
    - synchronous path inside ``execute_turn`` when the prefetch settles
      within the bounded wait window;
    - mid-loop consume in ``agent_loop`` when the prefetch settles after
      the first model request started.

    When ``registry`` is a SourceRegistry (citations gate on), each recalled
    item is registered as a ``memory`` source and its line is prefixed with the
    short handle (``[m1] …``) so the model can cite it; the item id is kept
    engine-private in the record's ``ref``. With ``registry=None`` the output is
    byte-identical to before.
    """

    def label(text: str, ref: str) -> str:
        if registry is None:
            return ""
        handle = registry.register("memory", {"points": [text]}, ref=ref)
        return f"[{handle}] "

    episodes = bundle.get("episodes") or []
    knowledge = bundle.get("knowledge") or []
    social = bundle.get("social") or []
    parts: list[str] = []
    if episodes:
        capped = cap_search_results(episodes, key="description")
        if capped:
            lines = ["Relevant past context:"]
            for ep in capped:
                text = f"{ep.get('key', '')}: {ep.get('description', '')}"
                lines.append(f"- {label(text, str(ep.get('id', '')))}{text}")
            parts.append("\n".join(lines))
    if knowledge:
        capped = cap_search_results(knowledge, key="content")
        if capped:
            lines = ["Known facts:"]
            for k in capped:
                text = k.get("content", k.get("key", ""))
                lines.append(f"- {label(text, str(k.get('id', '')))}{text}")
            parts.append("\n".join(lines))
    if social:
        lines = ["People context:"]
        for p in social[:3]:
            text = p.get("name", "Unknown")
            lines.append(f"- {label(text, str(p.get('id', '')))}{text}")
        parts.append("\n".join(lines))
    if not parts:
        return None
    return "<system-reminder>" + "\n\n".join(parts) + "</system-reminder>"


def register_tool_source(tool_name: str, result_text: str, registry) -> str:
    """Register an asset/web tool result as a citable source and return the
    result prefixed with its short ``[handle]`` so the model can cite it.

    Asset (``ad:{server}.{action}``) → an ``asset`` source (name = the connector,
    info = the result). Web (``web_search`` → a JSON list of ``{title,url}``) → a
    ``web`` source carrying the links (no content). Any other tool passes through
    unchanged. The connection_id is NOT threaded here — it stays inside the AD
    layer (engine-private); the asset record's ``ref`` is left empty for v1.

    Pure: registers into the registry, never emits. The CALLER labels only the
    model-visible copy of the result, never the wire tool block, so the handle is
    never displayed.
    """
    if registry is None or not result_text:
        return result_text
    if tool_name.startswith("ad:"):
        server = tool_name[3:].split(".", 1)[0]
        handle = registry.register("asset", {"name": server, "info": result_text[:500]})
        return f"[{handle}] {result_text}"
    if tool_name == "web_search":
        try:
            items = json.loads(result_text)
        except Exception:
            return result_text
        if not isinstance(items, list):
            return result_text
        links = [
            {"title": str(it.get("title", "")), "url": str(it.get("url", ""))}
            for it in items
            if isinstance(it, dict) and it.get("url")
        ]
        if not links:
            return result_text
        handle = registry.register("web", {"links": links})
        return f"[{handle}] {result_text}"
    return result_text


def complexity_score(text: str) -> int:
    """Heuristic complexity score — no LLM call, pure string analysis."""
    score = 0
    lower = text.lower()
    for phrase in ("first", "then", "after that", "next", "finally", "step", "and then"):
        if phrase in lower:
            score += 2
    if len(text) > 500:
        score += 2
    elif len(text) > 200:
        score += 1
    score += max(0, lower.count("?") - 1)
    if re.search(r"\d+[.)]\s", text):
        score += 2
    return score


ReplanCallback = Callable[["Charter", str], Any]


def trace_mod():
    """Deferred import of execution_trace to avoid circular import."""
    from src.engine_core import execution_trace

    return execution_trace


def append_permission_content_block(builder: ToolResultBuilder, cb: dict) -> None:
    """Dispatch a single PermissionResult.content_blocks entry to the builder.

    Each entry is a provider-neutral content-block dict (mirroring
    Anthropic's ``ContentBlockParam``). Today the populator only emits
    image sources, but the type allows future text/document blocks.
    Dispatch on ``cb["type"]`` so adding new kinds doesn't require
    re-touching the call site in ``run_single_tool``.
    """
    block_type = cb.get("type") if isinstance(cb, dict) else None
    if block_type == "image":
        builder.append_image(cb.get("source", cb))
    elif block_type == "text":
        builder.append_text(cb.get("text", ""), label="permission")
    else:
        builder.append_image(cb)


def strip_thinking_blocks(messages: list) -> list:
    """Remove ThinkingBlock instances from message history.

    Required on model fallback — thinking signatures are key-bound
    and cause 400 errors when replayed to a different model.
    """
    from src.herald.types import Message as LLMMessage, ThinkingBlock

    result = []
    for msg in messages:
        if isinstance(msg.content, str):
            result.append(msg)
            continue
        filtered = [b for b in msg.content if not isinstance(b, ThinkingBlock)]
        if len(filtered) == len(msg.content):
            result.append(msg)
        else:
            result.append(
                LLMMessage(
                    role=msg.role,
                    content=filtered or "",
                    timestamp=msg.timestamp,
                )
            )
    return result


CRAFT_AUTHORING_GUIDE = """\
You can render UI directly by writing a single self-contained <craft> tag in your \
reply. A streaming parser turns it into a UI component the product renders; the tag \
itself is never shown to the user. Reach for it whenever something is better shown \
than told — a confirm card, a form, a table, a chart, a document, a dashboard.

Syntax (one tag, opened and closed in your output):
<craft surface="inline|panel" format="html|markdown|code|svg|mermaid|react" wait="true|false" key="kebab-id" title="Human title">
  ...the component source (HTML, or React for reactive UIs)...
</craft>

Attributes:
- surface: "inline" (in the conversation flow) or "panel" (a set-apart window/canvas).
- format: "html" for most UI; "react" for reactive components; "markdown"/"code"/"svg"/"mermaid" as needed (add language="..." for code).
- wait: "true" halts the turn for the user to act (a confirm); "false" shows it and keeps going (a banner/live view).
- key: a stable kebab-case id you choose; title: a short human-readable title.

Make an element actionable with craft-action="<command>": on click, the product \
sends that command back to you as the user's response, so you can continue the turn.

Live data (optional — for crafts that show or act on connected-asset data):
Add live behavior with a <craft-data> JSON island as the FIRST child of the craft:
<craft surface="panel" format="react" key="dispatch" title="Live Dispatch">
  <craft-data>
  {"subscribe":[{"as":"trucks","server_name":"sap_fsm","connection_id":"conn_A","op":"list_trucks","args":{},"poll_s":2}],
   "actions":{"reassign":{"route":"direct","connection_id":"conn_A","op":"reassign_route"},"rebalance":{"route":"engine"}}}
  </craft-data>
  ...component source: bind elements to data and emit actions...
</craft>
- subscribe: each entry is one live read — as (a local alias the component reads \
by), server_name, connection_id (the credentialed account), op (the bare \
read-only MCP tool name), args (the query), poll_s (seconds; null = fetch once). \
Only subscribe an op that is read-only, re-pollable, and quota-safe — if a data \
source is not safely pollable, do not subscribe it.
- actions: name → {route}. route "direct" is a mechanical asset write (include its \
connection_id + write op); route "engine" wakes you to reason. Default to "engine" \
when uncertain or for high-blast-radius / irreversible actions.
- In the component, bind an element to live data with craft-bind="<alias>" and fire \
an action with craft-emit="<name>" route="direct|engine".
- When a control fires a route:engine action, have the component include its \
current state snapshot in the action's value, so you reason over exactly what the \
user saw. Do NOT re-emit the craft for a data change — its polling updates it; \
re-author the same key only for a real edit (a new control or data point).

Rules: write a <craft> only at the top level of your reply. A <craft> shown inside \
a ``` fenced code block or `inline code` is treated as documentation and is NOT \
produced — use that to explain the tag. Do not describe the tag in prose; just \
write it where the UI should appear.
"""


def compose_craft_guidance() -> str:
    """The craft-authoring system-prompt section.

    Injected only when the craft gate is on. When off, the model is never told
    crafts exist, so it writes plain prose — no generate-then-scrub.
    """
    return CRAFT_AUTHORING_GUIDE


CITATION_GUIDE = """\
<citations>
Some context items are labeled with a short source handle in square brackets at \
the start of the line — e.g. `[m1]`, `[a1]`, `[w1]`. When a clause in your reply \
is derived from such a source, append a citation marker immediately after that \
clause:

    We're targeting Aug 14⟦cite:m1⟧ for launch.

Rules:
- Use the exact handle from the context label. Never invent a handle.
- Put the marker right after the clause it supports — not at the end of the message.
- Multiple sources for one clause: ⟦cite:m1,w2⟧.
- Only add a marker when the clause genuinely came from that source; uncited prose is fine.
- The markers are stripped from what the user sees; they only attach the source.
</citations>"""


def compose_citation_guidance() -> str:
    """The citation system-prompt section.

    Injected only when the citations gate is on. When off, the model is never
    told the markers exist, so it writes plain prose — nothing to strip.
    """
    return CITATION_GUIDE


class ExecutionThread:
    """Unified agentic runtime — persistent per task, reused across messages."""

    def __init__(
        self,
        adapter: LLM,
        emitter: EventEmitter,
        memory: WorkingMemoryBackend,
        tools: ToolRegistry | None = None,
        resume_state_mgr: "ResumeStateManager | None" = None,
        replan_callback: ReplanCallback | None = None,
        ask_user_callback: AskUserCallback | None = None,
        settings: Any = None,
        consciousness: Any = None,
        consciousness_mgr: Any = None,
        search: Any = None,
        utility: Any = None,
        assets: Any = None,
        skill_catalog: Any = None,
        data_dir: str = "",
        conversation_store: Any = None,
        signal_store: Any = None,
    ) -> None:
        self.adapter = adapter
        self.emitter = emitter
        self.memory = EmittingMemory(memory, emitter)
        self.tools = tools
        self.resume_state_mgr = resume_state_mgr
        self.replan_callback = replan_callback
        self.ask_user_callback = ask_user_callback
        self.settings = settings
        self.consciousness = consciousness
        self.consciousness_mgr = consciousness_mgr
        self.search = search
        self.utility = utility
        self.assets = assets
        self.skill_catalog = skill_catalog
        self.data_dir = data_dir
        self.conversation_store = conversation_store
        self.signal_store = signal_store
        self.scratchpad: dict[str, Any] = {}
        self.cancel_requested: bool = False
        self.pinned: bool = False
        self.charter: Charter | None = None
        self.tools_frozen: bool = False
        self.session_charter: Charter | None = None
        self.last_injected_charter: Charter | None = None
        self.turn_lock = asyncio.Lock()
        self.sub_agent_results: list = []
        self.replan_state: dict = {"triggered": False}
        self.planner_gate = asyncio.Event()
        self.planner_gate.set()
        self.sub_agent_count: int = 0
        self.connected_services_text: str = ""
        self.deferred_ad_tools: dict[str, ToolDef] = {}
        self.ad_connection_choice: dict[str, str] = {}
        self.deny_rules: list[PermissionRule] = []
        self.session_rules: list[PermissionRule] = []
        self.permission_store: PermissionStore | None = None
        self.permission_mode: PermissionMode = PermissionMode.DEFAULT
        self.denial_tracker = DenialTracker()
        self.sub_agents_done = asyncio.Event()
        self.sub_agents_done.set()
        self.active_sub_agents: list[Any] = []
        self.compactor: Any = None
        self.last_turn_budget: int | None = None
        self.pre_tool_hooks: PreToolUseHookRegistry = PreToolUseHookRegistry()
        self.post_tool_hooks: PostToolUseHookRegistry = PostToolUseHookRegistry()
        self.stop_hooks: StopHookRegistry = StopHookRegistry()
        self.tool_hook_prevented: bool = False
        self.session_started: bool = False
        self.session_start_hooks: list[tuple[str, Any, float]] = []
        self.session_end_hooks: list[tuple[str, Any, float]] = []
        from src.engine_core.lifecycle_hooks import LifecycleHookBundle

        self.lifecycle_hooks: LifecycleHookBundle = LifecycleHookBundle()

        from src.engine_core.hook_settings import HookSettingsManager as HSM

        settings_load_flag = bool(
            self.settings is None or getattr(self.settings, "hooks_settings_load_enabled", True)
        )
        disable_env = os.environ.get(
            "INTELLIGENCE_ENGINE_DISABLE_HOOK_SETTINGS", ""
        ).strip().lower() in ("1", "true", "yes")
        hook_settings_enabled = settings_load_flag and not disable_env
        self.hook_settings_manager: HSM = HSM(
            pre_tool_registry=self.pre_tool_hooks,
            post_tool_registry=self.post_tool_hooks,
            stop_registry=self.stop_hooks,
            cwd=os.getcwd(),
            enabled=hook_settings_enabled,
            lifecycle_bundle=self.lifecycle_hooks,
        )
        try:
            self.hook_settings_manager.load()
        except Exception as exc:  # noqa: BLE001 — never fail thread init on bad settings
            logger.debug("HookSettingsManager.load() failed: %s", exc)
        self.results_dir_path: Path | None = None
        self.results_dir_finalizer: Any = None

        from src.engine_core import execution_trace as et
        from src.engine_core.schema import ThreadState

        self.ThreadState = ThreadState
        self.execution_trace: ExecutionTrace = et.ExecutionTrace()
        self.current_node_trace: NodeTrace | None = None
        self.thread_state: ThreadState = ThreadState.IDLE

    def register_session_hook(
        self,
        event: str,
        name: str,
        fn: Any,
        timeout_s: float = 60.0,
    ) -> None:
        """Register a session-lifecycle hook callable.

        ``event``:
            ``"SessionStart"`` or ``"SessionEnd"``.
        ``name``:
            Unique name; re-registration with the same name replaces the prior entry.
        ``fn``:
            Async callable ``async () -> None`` (or any awaitable; return value ignored).
        ``timeout_s``:
            Per-hook timeout.  Exceeded hooks are logged and skipped (fail-open).
        """
        entry = (name, fn, timeout_s)
        if event == "SessionStart":
            self.session_start_hooks = [h for h in self.session_start_hooks if h[0] != name]
            self.session_start_hooks.append(entry)
        elif event == "SessionEnd":
            self.session_end_hooks = [h for h in self.session_end_hooks if h[0] != name]
            self.session_end_hooks.append(entry)
        else:
            logger.debug("register_session_hook: unknown event '%s' — ignored", event)

    async def fire_session_hooks(self, hooks: list[tuple[str, Any, float]]) -> None:
        """Fire all hooks in the list concurrently (fail-open, with per-hook timeout)."""
        if not hooks:
            return

        async def run(name: str, fn: Any, timeout_s: float) -> None:
            try:
                await asyncio.wait_for(fn(), timeout=timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("session hook '%s' failed: %s (fail-open)", name, exc)

        await asyncio.gather(*(run(n, f, t) for n, f, t in hooks))

    async def session_end(self) -> None:
        """Fire SessionEnd hooks.  Call once when the session is being torn down."""
        await self.fire_session_hooks(self.session_end_hooks)

    def current_permission_rules(self) -> list[PermissionRule]:
        """The effective operator rule set for this thread, right now.

        Settings-derived deny rules, plus persisted store rules, plus rules
        added this session by an "always" choice. Read fresh on every call so a
        rule added mid-session applies to the next check, and shared with every
        sub-agent this thread spawns so the two cannot drift: a rule that binds
        the parent must bind the children running the same tools.
        """
        store_rules = self.permission_store.get_rules() if self.permission_store else []
        return self.deny_rules + store_rules + self.session_rules

    def ensure_tools(self) -> ToolRegistry:
        """Lazily initialize tool registry if needed."""
        if self.tools is None:
            self.tools = ToolRegistry()
        return self.tools

    async def ensure_compactor(self) -> None:
        """Lazily initialize the compaction orchestrator (persists across execute() calls).

        Async because ContextCollapseManager.restore_state() reads from
        SQLite, and we want the collapse state for a resumed session to
        be loaded before the first turn runs. Restoration is
        best-effort — a missing row or a read failure leaves the
        manager in a clean empty state.
        """
        if self.compactor is not None:
            return
        from src.engine_core.compaction_orchestrator import CompactionOrchestrator
        from src.engine_core.context_collapse import ContextCollapseManager

        db = getattr(self.conversation_store, "db", None) if self.conversation_store else None

        collapse_enabled = os.environ.get("FEATURE_CONTEXT_COLLAPSE", "true").lower() != "false"
        collapse_manager = ContextCollapseManager(
            db=db,
            enabled=collapse_enabled,
        )
        if collapse_enabled:
            thread_id = getattr(self, "current_thread_id", "")
            if thread_id:
                try:
                    await collapse_manager.restore_state(thread_id)
                except Exception:
                    logger.debug(
                        "context_collapse: restore_state failed (non-fatal)",
                        exc_info=True,
                    )

        self.compactor = CompactionOrchestrator(
            self.adapter,
            self.emitter,
            self.memory,
            consciousness_mgr=self.consciousness_mgr,
            db=db,
            search=self.search,
            utility=self.utility,
            assets=self.assets,
            collapse_manager=collapse_manager,
            skill_catalog=self.skill_catalog,
            lifecycle_bundle=self.lifecycle_hooks,
        )

    @property
    def state(self) -> Any:
        """Current thread lifecycle state."""
        return self.thread_state

    async def set_state(self, state: Any, thread_id: str = "") -> None:
        """Transition thread state and write to WM for observability."""
        self.thread_state = state
        tid = thread_id or getattr(self, "current_thread_id", "")
        if tid:
            try:
                await self.memory.write(
                    tid,
                    EntryType.SYSTEM_EVENT,
                    json.dumps(
                        {
                            "event": "thread_state_change",
                            "state": state.value,
                        }
                    ),
                )
            except Exception as e:
                logger.debug("WM write (state change) failed: %s", e)

    @property
    def results_dir(self) -> Path:
        """Per-thread temp dir for oversized tool results spilled to disk.

        Lazy-created on first access via ``tempfile.mkdtemp``. Cleaned
        up by a ``weakref.finalize`` callback registered at creation
        time, so the dir is removed when this Thread instance is
        garbage collected — no caller-facing close() needed.
        """
        if self.results_dir_path is None:
            import shutil
            import tempfile
            import weakref

            from src.infra.september_home import resolve_september_home, september_paths

            parent = september_paths(resolve_september_home())["results"]
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.results_dir_path = Path(
                tempfile.mkdtemp(prefix="thread-results-", dir=str(parent))
            )
            self.results_dir_finalizer = weakref.finalize(
                self,
                shutil.rmtree,
                str(self.results_dir_path),
                True,
            )
        return self.results_dir_path


    ASK_USER_VALID_FORMATS: frozenset[str] = frozenset({"choice", "multichoice", "confirm"})
    ASK_USER_DEFAULT_CONFIRM_OPTIONS: list[dict] = [
        {"label": "Yes", "value": "yes"},
        {"label": "No", "value": "no"},
    ]

    @staticmethod
    def normalize_options(raw: list) -> list[dict]:
        """Normalize a list of option strings or dicts to [{label, value}].

        Accepts either ``["yes", "no"]`` or ``[{"label": "Yes", "value": "yes"}]``
        from the LLM. Engine emits the structured form on the wire.
        """
        out: list[dict] = []
        for opt in raw or []:
            if isinstance(opt, dict):
                label = str(opt.get("label", "") or opt.get("value", "")).strip()
                value = str(opt.get("value", "") or opt.get("label", "")).strip()
            else:
                label = str(opt).strip()
                value = label
            if label:
                out.append({"label": label[:80], "value": value[:80]})
        return out

    async def await_user_input(
        self,
        question: str,
        format: str = "confirm",
        options: list[dict] | None = None,
        *,
        asset_hint: str = "",
    ) -> str:
        """Single owner of WAITING_INPUT → callback → EXECUTING.

        Both the LLM-facing ask_user tool handler and engine-internal
        ``escalate_to_user`` route through this so the state transition
        and the callback contract can never drift.
        """
        cb = self.ask_user_callback or default_ask_user
        params: dict = {
            "question": question,
            "format": format,
            "options": options or [],
        }
        if asset_hint:
            params["asset_hint"] = asset_hint
        await self.set_state(self.ThreadState.WAITING_INPUT)
        try:
            reply = await cb(params)
        finally:
            await self.set_state(self.ThreadState.EXECUTING)
        return flatten_ask_reply(reply)

    async def request_ad_approval(
        self,
        server_name: str,
        action_name: str,
        params: dict[str, Any],
        connection_id: str | None = None,
    ) -> bool:
        """Assets.build_tool's on_approval_needed callback for 'ask'-gated AD actions.

        Pauses the thread and asks the user via the same block-input path as
        ask_user / sandbox permission prompts (await_user_input ->
        ask_user_callback -> Coordinator.await_block_input -> CraftRequest ->
        pending_inputs, resolved by resume_block) - no new wire event, no new
        pause/resume plumbing. asset_hint carries server_name so the client
        can render "server X wants to run Y" the same way it already renders
        asset_required gaps.

        Three replies are offered: one-time Allow, Always allow, and Deny. An
        "always allow" reply additionally upgrades the stored per-tool policy
        (connection_permissions) for this (connection_id, action_name) from
        'ask' to 'allow' via permissions_store.set_behavior(set_by='user') and
        drops the live behavior cache, so the same action does not prompt again
        on the next tool build. A managed-locked row is never loosened (the
        upgrade is skipped and the row stays 'ask'); this one call is still
        allowed. A plain one-time Allow does NOT persist.

        Fails CLOSED: any exception, or a timeout (72h, same as ask_user),
        denies rather than silently executing a gated action.
        """
        preview = ""
        try:
            if params:
                raw = json.dumps(params, default=str, sort_keys=True)
                preview = f" with {raw[:200]}{'…' if len(raw) > 200 else ''}"
        except (TypeError, ValueError):
            preview = ""
        account_label = await self.lookup_account_label(connection_id)
        actor = f"'{server_name}'" + (f" ({account_label})" if account_label else "")
        question = f"{actor} wants to run {action_name}{preview}. Allow this action?"
        await self.emitter.emit(
            PermissionRequestEvent(
                thread_id=getattr(self, "current_thread_id", ""),
                server_name=server_name,
                action_name=action_name,
                params=params,
                connection_id=connection_id,
                account_label=account_label,
            )
        )
        try:
            reply = await self.await_user_input(
                question,
                format="confirm",
                options=[
                    {"label": "Allow", "value": "allow"},
                    {"label": "Always allow", "value": "always_allow"},
                    {"label": "Deny", "value": "deny"},
                ],
                asset_hint=server_name,
            )
        except Exception as e:
            logger.warning(
                "AD approval prompt failed for %s.%s: %s - denying (fail-closed)",
                server_name,
                action_name,
                e,
            )
            return False
        normalized = reply.strip().lower()
        always = normalized in ("always_allow", "always", "allow_always")
        approved = always or normalized in ("allow", "yes", "y", "approve")
        return approved

    def permission_request_hook_fn(self):
        """The PermissionRequest hook, as a callable check_permission can use.

        check_permission has always accepted ``permission_request_fn`` and had a
        complete branch for it, and nothing anywhere passed one - so a headless
        run had exactly one behaviour available for an ask-tier action: fail
        closed. An operator could not delegate the decision to policy code
        without opening the whole run to BYPASS. Returns None when no hook is
        registered, which keeps the fail-closed default untouched.
        """
        bundle = getattr(self, "lifecycle_hooks", None)
        if bundle is None:
            return None
        try:
            if not bundle.has_hooks(HookEvent.PERMISSION_REQUEST):
                return None
            registry = bundle.registry(HookEvent.PERMISSION_REQUEST)
        except Exception:
            return None

        async def run_hook(payload: dict) -> str | None:
            agg = await registry.execute_all(payload)
            decision = getattr(agg, "permission_decision", None)
            if decision is None and getattr(agg, "prevent_continuation", False):
                return "deny"
            return decision

        return run_hook

    async def lookup_account_label(self, connection_id: str | None) -> str:
        """The human identifier of the account behind a connection, or "".

        Best-effort by design: a missing label must degrade the prompt's wording,
        never block or fail an approval that would otherwise be shown.
        """
        if not connection_id:
            return ""
        assets = getattr(self, "assets", None)
        db = getattr(assets, "db", None) if assets is not None else None
        if db is None:
            return ""
        try:
            row = await db.fetchrow(
                "SELECT account_label, label FROM connections WHERE connection_id = ?",
                (connection_id,),
            )
        except Exception:
            logger.debug("account label lookup failed for %s", connection_id, exc_info=True)
            return ""
        if row is None:
            return ""
        return str(row["account_label"] or row["label"] or "")

    def register_ask_user_tool(
        self,
        callback: AskUserCallback | None = None,
    ) -> None:
        """Register ask_user tool — asks the user a question and waits."""
        self.ensure_tools()
        if self.ensure_tools().get_tool("ask_user") is not None:
            return
        if callback is not None:
            self.ask_user_callback = callback

        async def handler(params: dict) -> str:
            question = (params.get("question") or "").strip()
            if not question:
                return "Error: no question provided"
            fmt = (params.get("format") or "confirm").lower()
            if fmt not in self.ASK_USER_VALID_FORMATS:
                return (
                    f"Error: invalid format {fmt!r}. Must be one of: choice, multichoice, confirm."
                )
            options = self.normalize_options(params.get("options") or [])
            if fmt == "confirm":
                if not options:
                    options = list(self.ASK_USER_DEFAULT_CONFIRM_OPTIONS)
                elif len(options) != 2:
                    return (
                        "Error: format='confirm' takes exactly 2 options "
                        "(or omit to default to Yes/No)."
                    )
            else:
                if not (2 <= len(options) <= 4):
                    return f"Error: format={fmt!r} requires 2-4 options; got {len(options)}."
            return await self.await_user_input(question, format=fmt, options=options)

        self.ensure_tools().register(
            ToolDef(
                name="ask_user",
                description=(
                    "Ask the user a question and wait for their answer. Pick a "
                    "format that matches the question's intent — the UI renders "
                    "each format with appropriate controls.\n\n"
                    "FORMATS:\n"
                    "  - 'choice'      Single pick from 2-4 options (radio buttons).\n"
                    "  - 'multichoice' Multiple picks from 2-4 options (checkboxes).\n"
                    "  - 'confirm'     Yes/no decision. Options default to "
                    "[Yes, No] if omitted.\n\n"
                    "OPTIONS: 2-4 entries, each ≤80 chars. Each entry can be a "
                    "plain string (used as both label and value) or "
                    "{label, value} dict.\n\n"
                    "IMPORTANT: do NOT list 'Other' in options. The UI always "
                    "shows a free-text 'Other / type your own' input alongside "
                    "the structured controls; the user types there if your "
                    "options don't cover their answer."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question to ask the user. Be specific and concise.",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["choice", "multichoice", "confirm"],
                            "description": (
                                "Rendering intent. 'choice' = pick one of 2-4. "
                                "'multichoice' = pick any of 2-4. 'confirm' = "
                                "yes/no (default if omitted)."
                            ),
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 80},
                            "minItems": 2,
                            "maxItems": 4,
                            "description": (
                                "2-4 options. Required for 'choice' and "
                                "'multichoice'. Optional for 'confirm' "
                                "(defaults to [Yes, No])."
                            ),
                        },
                    },
                    "required": ["question", "format"],
                },
                handler=handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )

    MAX_SUB_AGENTS = 5

    PARENT_STATE_TOOLS: frozenset[str] = frozenset({"ask_user", "spawn_thread"})

    def register_checkpoint_tool(
        self,
        charter: Charter,
        trace_watermarks: dict | None = None,
    ) -> dict[str, NodeCheckpoint]:
        """Register the report_checkpoint tool and return the checkpoint tracker dict.

        No-op when charter is None — tool is registered but does nothing useful.
        """
        checkpoints: dict[str, NodeCheckpoint] = {}

        for node in charter.nodes:
            checkpoints[node.node_id] = NodeCheckpoint(
                node_id=node.node_id,
                label=node.label,
                status="pending",
                output="",
            )

        async def checkpoint_handler(params: dict) -> str:
            node_id = params.get("node_id", "")
            status = params.get("status", "completed")
            output = params.get("output", "")

            if node_id not in checkpoints:
                return f"Error: unknown node_id '{node_id}'"

            cp_timestamp = datetime.now(timezone.utc).isoformat()
            checkpoints[node_id] = NodeCheckpoint(
                node_id=node_id,
                label=checkpoints[node_id].label,
                status=status,
                output=output,
                timestamp=cp_timestamp,
            )

            if trace_watermarks is not None and self.current_node_trace is not None:
                et = trace_mod()
                buf = self.current_node_trace
                wm = trace_watermarks
                node_trace = et.NodeTrace(
                    node_id=node_id,
                    agent_name=checkpoints[node_id].label,
                    started_at=wm.get("last_checkpoint_at", ""),
                    completed_at=cp_timestamp,
                    agent_output=output[:2000],
                )
                node_trace.tool_calls = buf.tool_calls[wm["tool_calls"] :]
                node_trace.skill_calls = buf.skill_calls[wm["skill_calls"] :]
                node_trace.asset_calls = buf.asset_calls[wm["asset_calls"] :]
                node_trace.ask_user_interactions = buf.ask_user_interactions[wm["ask_user"] :]
                wm["tool_calls"] = len(buf.tool_calls)
                wm["skill_calls"] = len(buf.skill_calls)
                wm["asset_calls"] = len(buf.asset_calls)
                wm["ask_user"] = len(buf.ask_user_interactions)
                wm["last_checkpoint_at"] = cp_timestamp
                self.execution_trace.nodes.append(node_trace)

            thread_id = getattr(self, "current_thread_id", "")
            if thread_id:
                try:
                    await self.memory.write(
                        thread_id,
                        EntryType.CHECKPOINT,
                        json.dumps(
                            {
                                "node_id": node_id,
                                "label": checkpoints[node_id].label,
                                "status": status,
                                "output": output,
                                "timestamp": cp_timestamp,
                            }
                        ),
                    )
                except Exception as e:
                    logger.debug("WM write (node checkpoint %s) failed: %s", node_id, e)

            await self.emitter.emit(
                NodeProgress(
                    thread_id=thread_id,
                    node_id=node_id,
                    status=status,
                    output_preview=output[:200],
                )
            )

            try:
                if thread_id:
                    await self.memory.write(
                        thread_id,
                        EntryType.SYSTEM_EVENT,
                        json.dumps(
                            {
                                "event": "checkpoint",
                                "node_id": node_id,
                                "status": status,
                                "output_preview": output[:200],
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("WM write (todo checkpoint) failed: %s", e)

            if self.resume_state_mgr is not None:
                try:
                    from src.engine_core.resume_state import ResumeState

                    mid_state = ResumeState(
                        charter_id=charter.charter_id,
                        thread_id=thread_id,
                        charter_version=charter.charter_version,
                        charter_json=charter.model_dump(),
                        node_status={nid: cp.status for nid, cp in checkpoints.items()},
                        partial_results={
                            nid: cp.output for nid, cp in checkpoints.items() if cp.output
                        },
                        scratchpad_snapshot=dict(self.scratchpad),
                    )
                    await self.resume_state_mgr.write_checkpoint(mid_state, pinned=self.pinned)
                except Exception as e:
                    logger.debug("Channel state mid-checkpoint write failed: %s", e)

            return f"Checkpoint recorded: {node_id} = {status}"

        self.ensure_tools()

        self.ensure_tools().register(
            ToolDef(
                name="report_checkpoint",
                description=(
                    "Report progress on a mission goal. Call this when you complete "
                    "a goal from your mission brief. Provide the node_id, status "
                    "(completed/skipped), and a brief output summary. "
                    "No-op if no mission goals are active."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "The node_id of the goal being reported on.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["completed", "skipped", "in_progress"],
                            "description": "Current status of this goal.",
                        },
                        "output": {
                            "type": "string",
                            "description": "Brief output or result of completing this goal.",
                        },
                    },
                    "required": ["node_id", "status"],
                },
                handler=checkpoint_handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )

        return checkpoints

    def register_replan_tool(
        self,
        replan_state: dict,
    ) -> None:
        """Register request_replan tool — always available, no-op without charter."""
        self.ensure_tools()
        if self.ensure_tools().get_tool("request_replan") is not None:
            return

        async def handler(params: dict) -> str:
            reason = params.get("reason", "Agent requested replan")

            charter = self.charter
            if charter is None:
                return "No active charter to replan. Use request_plan to create one first."

            await self.emitter.emit(
                ReplanRequested(
                    thread_id=getattr(self, "current_thread_id", ""),
                    charter_id=charter.charter_id,
                    reason=reason,
                )
            )

            try:
                tid = getattr(self, "current_thread_id", "")
                if tid:
                    await self.memory.write(
                        tid,
                        EntryType.SYSTEM_EVENT,
                        json.dumps(
                            {
                                "charter_id": charter.charter_id,
                                "reason": reason,
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("WM write (replan request) failed: %s", e)

            if self.replan_callback is not None:
                result = await self.replan_callback(charter, reason)
                if result is not None:
                    new_charter, new_tools = result
                    replan_state["triggered"] = True
                    replan_state["new_charter"] = new_charter
                    replan_state["new_tools"] = new_tools
                    return f"Replan accepted. New charter provided with {len(new_charter.nodes)} goals."

            return "Replan denied or failed. Continue with current approach."

        self.ensure_tools().register(
            ToolDef(
                name="request_replan",
                description=(
                    "Request a replan when you need to expand your operational scope "
                    "beyond the current goals, when the current approach is failing, "
                    "or when the mission goals need revision. No-op if no charter is active."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why a replan is needed.",
                        },
                        "context": {
                            "type": "string",
                            "description": "What was tried and what failed.",
                        },
                        "completed_goals": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Node IDs of goals already completed.",
                        },
                    },
                    "required": ["reason"],
                },
                handler=handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )

    def register_gap_resolution_tool(self) -> None:
        """Register gap_resolution tool — always available.

        Handles info_gap, action_gap, and asset_required gap types.
        Info gaps escalate to the user. Action gaps go directly to the user.
        Asset gaps notify the product layer and ask user to connect.
        """
        self.ensure_tools()
        if self.ensure_tools().get_tool("gap_resolution") is not None:
            return

        async def escalate_to_user(
            question: str,
            gap_type: GapType,
            fallback_error: str,
            asset_hint: str = "",
            options: list[dict] | None = None,
        ) -> str:
            """Ask the user via the shared input pipeline.

            Routes through ``await_user_input`` directly (not through the registered
            ask_user tool) so the WAITING_INPUT state mutation lives in one
            place. Internal escalations always use ``format='confirm'`` —
            the rich choice/multichoice formats exist for the LLM-facing tool.
            """
            try:
                answer = await self.await_user_input(
                    question,
                    format="confirm",
                    options=options or list(self.ASK_USER_DEFAULT_CONFIRM_OPTIONS),
                    asset_hint=asset_hint,
                )
            except Exception as e:
                logger.warning("User-input escalation failed: %s", e)
                return fallback_error
            return answer

        async def handler(params: dict) -> str:
            question = params.get("question", "")
            gap_type = params.get("gap_type", "info_gap")

            if gap_type == "asset_required":
                server_name = params.get("server_name", question)
                connection_id: str | None = None
                oauth_url: str | None = None
                if self.assets is not None:
                    try:
                        resolved = await self.assets.ad.resolve_asset_required(server_name)
                        connection_id = resolved.get("connection_id")
                        oauth_url = resolved.get("oauth_url")
                    except Exception as e:
                        logger.warning("resolve_asset_required failed for %s: %s", server_name, e)
                await self.emitter.emit(
                    AssetRequiredEvent(
                        thread_id=getattr(self, "current_thread_id", ""),
                        server_name=server_name,
                        connection_id=connection_id,
                        oauth_url=oauth_url,
                    )
                )
                return await escalate_to_user(
                    question=f"This task requires '{server_name}' to be connected. "
                    "Please connect it and confirm when ready.",
                    gap_type=GapType.ASSET_REQUIRED,
                    fallback_error=f"Error: asset '{server_name}' is required but not connected",
                    asset_hint=server_name,
                    options=[
                        {"label": "Connected", "value": "connected"},
                        {"label": "Cancel", "value": "cancel"},
                    ],
                )

            if gap_type == "action_gap":
                return await escalate_to_user(
                    question=question,
                    gap_type=GapType.ACTION_GAP,
                    fallback_error="Error: action gap escalation failed",
                )

            return await escalate_to_user(
                question=question,
                gap_type=GapType.INFO_GAP,
                fallback_error="Error: info gap escalation failed",
            )

        self.ensure_tools().register(
            ToolDef(
                name="gap_resolution",
                description=(
                    "Resolve information gaps, request user approval, or request "
                    "asset connections. Use gap_type='info_gap' when you need "
                    "information. Use gap_type='action_gap' when you need user "
                    "approval for an action. Use gap_type='asset_required' when "
                    "you need an external service connected."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question or request.",
                        },
                        "gap_type": {
                            "type": "string",
                            "enum": ["info_gap", "action_gap", "asset_required"],
                            "description": "Type of gap: info_gap (need information), action_gap (need approval), or asset_required (need an external service connected).",
                        },
                        "server_name": {
                            "type": "string",
                            "description": "Server/service name when gap_type is asset_required (e.g. 'gmail', 'slack').",
                        },
                    },
                    "required": ["question", "gap_type"],
                },
                handler=handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )

    def register_list_content_tool(self) -> None:
        """Register list_content tool — discover available content in task's content store."""
        self.ensure_tools()
        if self.ensure_tools().get_tool("list_content") is not None:
            return

        async def handler(params: dict) -> str:
            source = params.get("source", "")
            pattern = params.get("pattern", "")
            tid = getattr(self, "current_thread_id", "")
            entries: list = []
            if self.resume_state_mgr:
                entries.extend(
                    await self.resume_state_mgr.list_content(tid, source=source, pattern=pattern)
                )
            return json.dumps({"entries": entries, "total_count": len(entries)}, ensure_ascii=False)

        self.ensure_tools().register(
            ToolDef(
                name="list_content",
                description=(
                    "Discover what content is available in the task's content store. "
                    "Use this BEFORE search_content to see what's available. "
                    "Returns metadata and a short preview for each entry — not full content. "
                    "Filter by source (craft/document/tool_result/media/upload/paste) or title pattern."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Filter by source: 'craft', 'document', 'tool_result', 'media', or omit for all.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Filter by title substring (case-insensitive).",
                        },
                    },
                },
                handler=handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )

    def register_search_content_tool(self) -> None:
        """Register search_content tool — search within stored content by pattern."""
        self.ensure_tools()
        if self.ensure_tools().get_tool("search_content") is not None:
            return

        async def handler(params: dict) -> str:
            pattern = params.get("pattern", "")
            if not pattern:
                return json.dumps({"error": "pattern is required"})
            tid = getattr(self, "current_thread_id", "")
            content_id = params.get("content_id", "")
            context_lines = params.get("context_lines", 3)
            output_mode = params.get("output_mode", "content")
            max_results = min(params.get("max_results", 10), 20)
            case_insensitive = params.get("case_insensitive", True)
            multiline = params.get("multiline", False)

            search_kwargs = dict(
                thread_id=tid,
                search_pattern=pattern,
                content_id=content_id,
                context_lines=context_lines,
                output_mode=output_mode,
                max_results=max_results,
                case_insensitive=case_insensitive,
                multiline=multiline,
            )

            key = (
                "sections"
                if output_mode == "sections"
                else ("counts" if output_mode == "count" else "matches")
            )
            all_items: list = []
            total_matches = 0
            searched = 0
            truncated = False
            is_count = output_mode == "count"

            if self.resume_state_mgr:
                cs = await self.resume_state_mgr.search_content(**search_kwargs)
                all_items.extend(cs.get(key, []))
                total_matches += cs.get("total_matches", 0)
                searched += cs.get("searched_entries", 0)
                truncated = truncated or bool(cs.get("truncated"))

            merged = {
                key: all_items,
                "total_matches": total_matches,
                "searched_entries": searched,
                "truncated": truncated,
            }
            return json.dumps(merged, ensure_ascii=False, default=str)

        self.ensure_tools().register(
            ToolDef(
                name="search_content",
                description=(
                    "Search within stored content by text or regex pattern. "
                    "Use list_content first to see what's available. "
                    "Start with output_mode 'count' to check match volume, "
                    "then 'content' for matching lines with context, "
                    "or 'sections' for full paragraphs containing matches. "
                    "Results capped at max_results (default 10, hard cap 20); "
                    "the response carries truncated:true when the cap cut it "
                    "short, so treat a truncated result as partial. "
                    "count mode is never capped and reports the true total."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Text or regex pattern to search for.",
                        },
                        "content_id": {
                            "type": "string",
                            "description": "Search within a specific content entry. Omit to search all.",
                        },
                        "context_lines": {
                            "type": "integer",
                            "description": "Lines of context before/after each match. Default: 3.",
                        },
                        "output_mode": {
                            "type": "string",
                            "enum": ["content", "count", "sections"],
                            "description": "Output mode: 'content' (lines + context), 'count' (match counts), 'sections' (full paragraphs).",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max matches to return. Default: 10, hard cap: 20.",
                        },
                        "case_insensitive": {
                            "type": "boolean",
                            "description": "Case-insensitive search. Default: true.",
                        },
                        "multiline": {
                            "type": "boolean",
                            "description": "Pattern can span across lines. Default: false.",
                        },
                    },
                    "required": ["pattern"],
                },
                handler=handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )


    def register_create_skill_tool(self) -> None:
        """Register create_skill tool — LLM creates user skills through conversation."""
        self.ensure_tools()
        if self.ensure_tools().get_tool("create_skill") is not None:
            return
        if not self.data_dir:
            return

        async def handler(params: dict) -> str:
            name = params.get("name", "")
            if not name:
                return "Error: name is required"
            description = params.get("description", "")
            if not description:
                return "Error: description is required"

            from src.catalog import create_user_skill

            data_dir = Path(self.data_dir)

            create_user_skill(
                data_dir,
                {
                    "name": name,
                    "description": description,
                    "category": params.get("category", "custom"),
                    "prompt": params.get("prompt", ""),
                    "capabilities": params.get("capabilities", []),
                    "scripts": params.get("scripts", {}),
                },
            )

            if self.skill_catalog:
                self.skill_catalog.reload_user_skills(data_dir / "skills")

            return json.dumps(
                {
                    "status": "created",
                    "name": name,
                    "message": f"Skill '{name}' created and available.",
                }
            )

        self.ensure_tools().register(
            ToolDef(
                name="create_skill",
                description="Create a reusable skill. Use when the user asks to create, save, or build a skill/workflow. Gather requirements via ask_user first.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name (kebab-case)"},
                        "description": {
                            "type": "string",
                            "description": "When to use this skill. Include trigger phrases.",
                        },
                        "category": {"type": "string", "description": "Category"},
                        "prompt": {"type": "string", "description": "Prompt template"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                        "scripts": {"type": "object", "description": "Scripts: {filename: code}"},
                    },
                    "required": ["name", "description"],
                },
                handler=handler,
            )
        )

    def register_tool_search(self) -> None:
        """Register tool_search — discover deferred AD tool schemas.

        Deferred AD tools are registered as lightweight stubs (no input_schema)
        to keep the cached prefix stable. The model calls tool_search to get
        the full schema, then calls the deferred tool with correct params.
        """
        self.ensure_tools()
        if self.ensure_tools().get_tool("tool_search") is not None:
            return

        async def handler(params: dict) -> str:
            query = params.get("query", "").lower().strip()
            if not query:
                return json.dumps({"error": "query is required"})

            if not self.deferred_ad_tools:
                return json.dumps({"tools": [], "message": "No deferred tools available."})

            scored: list[tuple[int, str, dict]] = []
            for name, tool in self.deferred_ad_tools.items():
                score = score_tool_match(query, tool)
                if score > 0:
                    scored.append(
                        (
                            score,
                            tool.name,
                            {
                                "name": tool.name,
                                "description": tool.description,
                                "input_schema": tool.input_schema,
                            },
                        )
                    )
            scored.sort(key=lambda row: (-row[0], row[1]))
            truncated = len(scored) > TOOL_SEARCH_MAX_RESULTS
            matches = [row[2] for row in scored[:TOOL_SEARCH_MAX_RESULTS]]

            if not matches:
                available = list(self.deferred_ad_tools.keys())
                return json.dumps(
                    {
                        "tools": [],
                        "message": f"No match for '{query}'. Available deferred tools: {available}",
                    }
                )

            payload: dict[str, Any] = {"tools": matches}
            if truncated:
                payload["message"] = (
                    f"Showing the {TOOL_SEARCH_MAX_RESULTS} best matches of "
                    f"{len(scored)}. Narrow the query to see others."
                )
            return json.dumps(payload)

        self.ensure_tools().register(
            ToolDef(
                name="tool_search",
                description=(
                    "Search for deferred tool schemas. Some external tools are loaded as lightweight "
                    "stubs to save context. Call this with a keyword to get the full schema (name, "
                    "description, input parameters) so you can call the tool correctly."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keyword to search by tool name or description.",
                        },
                    },
                    "required": ["query"],
                },
                handler=handler,
                is_concurrency_safe=ALWAYS_SAFE,
                is_read_only=ALWAYS_SAFE,
            )
        )


    def build_skill_context(self, skills: list[dict]) -> str:
        """Build skill context for system prompt injection via skill_execute().

        Uses utility.skill_execute() as the single entry point for all skill
        types. Collects prompt text from each matched skill.
        """
        if not skills:
            return ""
        if not self.utility:
            return ""
        parts = ["Relevant skills for this task:"]
        for skill in skills:
            result = self.utility.skill_execute(skill)
            if result.get("prompt"):
                parts.append(result["prompt"])
        return "\n".join(parts) if len(parts) > 1 else ""


    TOKEN_BUDGET_TEXT = (
        'When the user specifies a token target (e.g., "+500k", '
        '"spend 2M tokens", "use 1B tokens"), your output token '
        "count will be shown each turn. Keep working until you "
        "approach the target \u2014 plan your work to fill it "
        "productively. The target is a hard minimum, not a "
        "suggestion. If you stop early, the system will "
        "automatically continue you."
    )

    def register_prompt_sections(self) -> None:
        """Register prompt sections through consciousness manager.

        Called once per session. Sections are resolved in registration order
        and split at the DYNAMIC_BOUNDARY marker into stable (globally
        cacheable) and dynamic (ephemeral) groups.
        """
        from src.memory.consciousness import SectionStability, DYNAMIC_BOUNDARY

        mgr = self.consciousness_mgr
        if mgr is None:
            return

        mgr.register_section(
            "identity",
            SectionStability.STABLE,
            lambda: self.consciousness.render_core() if self.consciousness else "",
        )
        if self.charter is not None:
            mgr.register_section(
                "mission",
                SectionStability.STABLE,
                lambda: self.build_mission_prompt(self.charter) if self.charter else "",
            )
            if self.session_charter is None:
                self.session_charter = self.charter
                self.last_injected_charter = self.charter
        if self.settings and getattr(self.settings, "token_budget_enabled", False):
            mgr.register_section(
                "token_budget",
                SectionStability.STABLE,
                lambda: self.TOKEN_BUDGET_TEXT,
            )
        mgr.register_section(
            "__boundary__",
            SectionStability.STABLE,
            lambda: DYNAMIC_BOUNDARY,
        )
        mgr.register_section(
            "connected_services",
            SectionStability.VOLATILE,
            lambda: self.connected_services_text,
        )
        mgr.register_section(
            "dynamic_context",
            SectionStability.VOLATILE,
            lambda: self.consciousness.render_context() if self.consciousness else "",
        )
        logger.debug("Prompt sections registered (%d sections)", len(mgr.sections))

    async def refresh_connected_services_snapshot(self) -> None:
        """Refresh ``connected_services_text`` from Search.ad_connections().

        Called from the async path before every turn's prompt build. The
        ``connected_services`` prompt section's sync callback then reads the
        cached string. Without this refresh, the section would render empty
        on a cold cache and the LLM would not see live tool affordances.
        """
        if self.search is None:
            self.connected_services_text = ""
            return
        try:
            ad_meta = await self.search.ad_connections()
        except Exception:
            logger.debug("connected_services refresh: ad_connections() failed", exc_info=True)
            self.connected_services_text = ""
            return

        active_servers = {
            c["server_name"]
            for c in ad_meta.get("connections", [])
            if c.get("status") == "ACTIVE" and bool(c.get("enabled", True)) and c.get("server_name")
        }
        if not active_servers:
            self.connected_services_text = ""
            return

        display_by_server = {
            s["server_name"]: (s.get("display_name") or s["server_name"])
            for s in ad_meta.get("servers", [])
            if s.get("server_name") in active_servers
        }
        instructions_by_server = {
            s["server_name"]: (s.get("instructions") or "")
            for s in ad_meta.get("servers", [])
            if s.get("server_name") in active_servers
        }
        actions_by_server = ad_meta.get("action_names_by_server", {})

        lines = ["CONNECTED SERVICES:"]
        for server_name in sorted(active_servers):
            display = display_by_server.get(server_name, server_name)
            actions = sorted(actions_by_server.get(server_name, []))
            tool_names = (
                ", ".join(f"ad:{server_name}.{a}" for a in actions)
                if actions
                else "(no tools registered)"
            )
            lines.append(f"  - {display} ({server_name}): {tool_names}")
            guidance = instructions_by_server.get(server_name, "").strip()
            if guidance:
                flattened = " ".join(guidance.split())
                lines.append(f"      notes from {server_name}: {flattened}")
        self.connected_services_text = "\n".join(lines)


    def build_mission_prompt(self, charter: Charter) -> str:
        """Build the mission system prompt from charter goals.

        Presents nodes as goals/checkpoints the agent should work toward,
        ordered by topological sort. Includes dependency info so the agent
        understands goal ordering.
        """
        sorted_nodes = topological_sort(charter)
        lines = [
            "You are an autonomous agent within an execution thread, executing a mission.",
            "You have full access to all available tools. You can:",
            "- Use any tool to accomplish your task",
            "- Call spawn_sub_agent to delegate SEPARABLE sub-tasks (one-shot, "
            "parallelizable, long-running/background, or a big isolated input that "
            "returns a compact summary) to specialized child workers. Keep "
            "conversational/iterative work that is tightly coupled to this thread's "
            "live state in-thread; the thread's own model is upgraded for hard turns.",
            "- Call request_replan if the current approach is failing and you need a new plan",
            "- Call report_checkpoint to mark mission goals as completed",
            "- Call gap_resolution to escalate information gaps or request user approval",
            "",
            "Your goals are listed below. Work through them in order.",
            "",
            "Goals:",
        ]
        node_map = {n.node_id: n for n in charter.nodes}
        for i, node in enumerate(sorted_nodes, 1):
            deps = [e.from_node for e in charter.edges if e.to_node == node.node_id]
            dep_labels = [node_map[d].label for d in deps if d in node_map]
            dep_str = f" (after: {', '.join(dep_labels)})" if dep_labels else ""
            lines.append(f"  {i}. [{node.node_id}] {node.label}{dep_str}")

        lines.append("")
        lines.append("Use report_checkpoint(node_id, status, output) after completing each goal.")
        lines.append("When all goals are complete, provide a final summary.")
        lines.append("")
        lines.append("These goals define your operational scope. You must work within them:")
        lines.append("- Complete each goal using whatever tools and approach you judge best.")
        lines.append(
            "- If a goal is unnecessary or already satisfied, mark it as skipped with report_checkpoint."
        )
        lines.append(
            "- If you need to do work that falls outside these goals, call request_replan first."
        )
        lines.append("  Do not take actions beyond your assigned scope without a revised charter.")
        lines.append(
            "- You have tactical freedom in HOW you achieve each goal, but not in WHAT goals you pursue."
        )
        return "\n".join(lines)

    def build_mission_reminder(self, charter: Charter, is_initial: bool = False) -> str:
        """Build a <system-reminder> payload for updated or new mission goals.

        Used when charter changes mid-session (replan or request_plan) instead
        of rebuilding the system prompt, preserving the cached prefix.
        """
        sorted_nodes = topological_sort(charter)
        node_map = {n.node_id: n for n in charter.nodes}
        if is_initial:
            preamble = (
                "MISSION ACTIVATED — You now have a structured mission. "
                "Work through the following goals in order."
            )
        else:
            preamble = (
                "MISSION UPDATE — Your goals have been revised. The following "
                "goals supersede any earlier mission goals."
            )
        lines = [preamble, "", "Goals:"]
        for i, node in enumerate(sorted_nodes, 1):
            deps = [e.from_node for e in charter.edges if e.to_node == node.node_id]
            dep_labels = [node_map[d].label for d in deps if d in node_map]
            dep_str = f" (after: {', '.join(dep_labels)})" if dep_labels else ""
            lines.append(f"  {i}. [{node.node_id}] {node.label}{dep_str}")
        lines.append("")
        lines.append("Use report_checkpoint(node_id, status, output) after completing each goal.")
        lines.append("When all goals are complete, provide a final summary.")
        return "\n".join(lines)


    async def initiate(self, thread_id: str = "") -> None:
        """Layer 1 — one-time setup. Called once when thread is created.

        Registers all tools, merges platform tools, provisions AD tools.
        After this, the thread is IDLE and ready for execute() calls.
        """
        thread_id = thread_id or f"thread-{uuid.uuid4().hex[:8]}"
        self.current_thread_id = thread_id
        await self.set_state(self.ThreadState.INITIALIZING, thread_id)

        self.sub_agent_results = []
        self.replan_state = {"triggered": False}
        self.register_ask_user_tool()
        self.register_replan_tool(self.replan_state)
        self.register_gap_resolution_tool()
        self.register_list_content_tool()
        self.register_search_content_tool()
        self.register_create_skill_tool()

        self.ensure_tools()
        if self.ensure_tools().get_tool("report_checkpoint") is None:

            async def checkpoint_stub(params: dict) -> str:
                if self.charter is None:
                    return "No mission goals active. Use request_plan first to create a charter."
                return (
                    "Charter exists but checkpoint handler not yet activated. This is unexpected."
                )

            self.ensure_tools().register(
                ToolDef(
                    name="report_checkpoint",
                    description=(
                        "Report progress on a mission goal. Call this when you complete "
                        "a goal from your mission brief. Provide the node_id, status "
                        "(completed/skipped), and a brief output summary. "
                        "No-op if no mission goals are active."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "node_id": {
                                "type": "string",
                                "description": "The node_id of the goal being reported on.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["completed", "skipped", "in_progress"],
                                "description": "Current status of this goal.",
                            },
                            "output": {
                                "type": "string",
                                "description": "Brief output or result of completing this goal.",
                            },
                        },
                        "required": ["node_id", "status"],
                    },
                    handler=checkpoint_stub,
                    is_concurrency_safe=lambda _: True,
                    is_read_only=lambda _: True,
                )
            )

        if self.utility and hasattr(self.utility, "platform_tools") and self.utility.platform_tools:
            platform_metas = sorted(
                self.utility.platform_tools.list_tools_meta(),
                key=lambda m: m.get("name", ""),
            )
            for meta in platform_metas:
                if self.ensure_tools().get_tool(meta["name"]) is None:
                    tool = self.utility.platform_tools.get_tool(meta["name"])
                    if tool:
                        self.ensure_tools().register(tool)

        self.register_tool_search()

        await self.provision_ad_tools()

        if self.tools:
            for meta in self.tools.list_tools_meta():
                tool = self.ensure_tools().get_tool(meta["name"])
                if tool and tool.name not in INTERNAL_TOOL_NAMES:
                    if tool.name in NETWORK_TOOL_NAMES:
                        tool.source = ToolSource.NETWORK
                    elif not tool.name.startswith("ad:"):
                        tool.source = ToolSource.PLATFORM

        if self.settings and hasattr(self.settings, "deny_rules"):
            for rule_dict in self.settings.deny_rules or []:
                self.deny_rules.append(
                    DenyRule(
                        tool=rule_dict.get("tool", "*"),
                        pattern=rule_dict.get("pattern", ""),
                        reason=rule_dict.get("reason", ""),
                        source=PermissionProvenance.USER.value,
                    )
                )

        if self.settings:
            user_path = Path(self.settings.permission_store_user_path).expanduser()
            self.permission_store = PermissionStore(user_path=user_path)
            self.permission_store.load()
            logger.info(
                "PermissionStore loaded: %d persistent rules",
                len(self.permission_store.get_rules()),
            )

        self.tools_frozen = True

        await self.set_state(self.ThreadState.IDLE, thread_id)
        logger.info(
            "Thread %s initiated: %d tools registered (frozen)",
            thread_id,
            self.tools.tool_count if self.tools else 0,
        )

    async def resolve_ad_connection(
        self, server_name: str, candidates: list[dict[str, Any]]
    ) -> str | None:
        """Pick which ACTIVE connection to use for ``server_name`` in this thread.

        ``candidates`` are the ACTIVE connection rows for this server_name, as
        returned by Search.ad_connections() (connection_id, status, provenance,
        provenance_rank, account_ref, owner_ref, created_at).

        Selection order:
          1. A choice already made earlier in this thread wins outright, as
             long as that connection is still among ``candidates``.
          2. Prefer the highest ``provenance_rank`` (managed > user > engine).
             A single connection at the top rank wins with no prompt - the
             common case (one connection, or a managed seat outranking a
             personal login).
          3. A tie at the top rank - e.g. two 'user' accounts of the same
             server - is genuinely ambiguous. Ask once, via the existing
             ask_user pipeline (await_user_input): no LLM turn, no tokens
             spent. The answer is cached on self.ad_connection_choice for
             the rest of the thread.
          4. If the prompt can't be answered (no callback wired, timeout,
             reply that doesn't match an offered connection_id, or more
             tied candidates than the 'choice' UI format shows cleanly),
             fall back deterministically to the most-recently-created
             connection at the top rank - provisioning must never block or
             error.
        """
        if not candidates:
            return None
        by_id = {c["connection_id"]: c for c in candidates if c.get("connection_id")}

        remembered = self.ad_connection_choice.get(server_name)
        if remembered and remembered in by_id:
            return remembered

        top_rank = max(c.get("provenance_rank", 0) for c in candidates)
        top = [c for c in candidates if c.get("provenance_rank", 0) == top_rank]

        if len(top) == 1:
            chosen = top[0]["connection_id"]
            self.ad_connection_choice[server_name] = chosen
            return chosen

        fallback = max(top, key=lambda c: c.get("created_at") or "")["connection_id"]

        if len(top) > 4:
            logger.info(
                "AD connection disambiguation for %s: %d tied-authority "
                "candidates, skipping prompt and using most recent",
                server_name,
                len(top),
            )
            self.ad_connection_choice[server_name] = fallback
            return fallback

        options = []
        for c in top:
            label_bits = [c.get("provenance") or "user"]
            if c.get("account_ref"):
                label_bits.append(c["account_ref"])
            elif c.get("owner_ref"):
                label_bits.append(c["owner_ref"])
            options.append(
                {
                    "label": f"{server_name} ({' · '.join(label_bits)})"[:80],
                    "value": c["connection_id"],
                }
            )

        try:
            reply = await self.await_user_input(
                f"Multiple connected accounts match '{server_name}'. "
                "Which one should I use for this session?",
                format="choice",
                options=options,
                asset_hint=server_name,
            )
        except Exception as e:
            logger.debug("AD connection disambiguation prompt failed for %s: %s", server_name, e)
            reply = ""

        chosen = reply if reply in by_id else fallback
        self.ad_connection_choice[server_name] = chosen
        return chosen

    async def on_asset_required(
        self,
        server_name: str,
        connection_id: str | None,
        oauth_url: str | None,
    ) -> None:
        """Bound as the ``on_asset_required`` callback threaded through
        Assets.build_tool - fires when a mid-run AD tool call hits
        AuthExpired (see assets.py handler's AuthExpired catch)."""
        await self.emitter.emit(
            AssetRequiredEvent(
                thread_id=getattr(self, "current_thread_id", ""),
                server_name=server_name,
                connection_id=connection_id,
                oauth_url=oauth_url,
            )
        )

    async def on_asset_action(
        self,
        server_name: str,
        action_name: str,
        params_preview: str,
        result_preview: str,
        duration_ms: int,
        status: str,
    ) -> None:
        """Bound as the ``on_action`` callback threaded through Assets.build_tool -
        fires after every attempted connector tool execution (success and both
        error paths) and emits the ``asset_action`` telemetry block."""
        await self.emitter.emit(
            AssetActionEvent(
                thread_id=getattr(self, "current_thread_id", ""),
                server_name=server_name,
                action_name=action_name,
                params_preview=params_preview,
                result_preview=result_preview,
                duration_ms=duration_ms,
                status=status,
            )
        )

    async def on_asset_status(
        self,
        server_name: str,
        connection_id: str | None,
        status: str,
        needs_auth: bool,
        oauth_url: str | None,
    ) -> None:
        """Bound as the ``on_status`` callback threaded through Assets.build_tool -
        fires on the mid-run AuthExpired path (alongside on_asset_required) and
        emits the ``asset_status`` connection-state block."""
        await self.emitter.emit(
            AssetStatusEvent(
                thread_id=getattr(self, "current_thread_id", ""),
                server_name=server_name,
                connection_id=connection_id,
                status=status,
                needs_auth=needs_auth,
                oauth_url=oauth_url,
            )
        )

    async def provision_ad_tools(self) -> int:
        """Provision AD tools from connected assets. Returns count of tools added.

        Uses filter_ad_tools_by_budget to cap total AD tool schema tokens.
        Tools that exceed the budget are registered as lightweight stubs
        (name + description only) to keep the cached prefix stable. Full schemas
        and handlers are stored in deferred_ad_tools for ToolSearch discovery.
        Deferred stubs proxy to the real handler when called.
        """
        from src.engine_core.context_engineering import filter_ad_tools_by_budget
        from src.herald.catalog import get_context_window

        all_ad_tools: list[ToolDef] = []
        if self.assets and self.search:
            try:
                ad_meta = await self.search.ad_connections()
                connections = ad_meta.get("connections", [])

                active_by_server: dict[str, list[dict[str, Any]]] = {}
                for conn in connections:
                    if conn.get("status") != "ACTIVE":
                        continue
                    if not bool(conn.get("enabled", True)):
                        continue
                    server_name = conn.get("server_name", "")
                    if server_name and conn.get("connection_id"):
                        active_by_server.setdefault(server_name, []).append(conn)

                for server_name, candidates in active_by_server.items():
                    connection_id = await self.resolve_ad_connection(server_name, candidates)
                    if connection_id:
                        ad_tools = await self.assets.build_tools_for_server(
                            connection_id,
                            server_name,
                            on_approval_needed=self.request_ad_approval,
                            on_asset_required=self.on_asset_required,
                            on_action=self.on_asset_action,
                            on_status=self.on_asset_status,
                        )
                        all_ad_tools.extend(ad_tools)
            except Exception as e:
                logger.debug("AD tool provisioning failed: %s", e)

        seen: set[str] = set()
        unique_ad_tools = []
        for tool in all_ad_tools:
            if tool.name in seen:
                logger.warning(
                    "SECURITY: refusing duplicate asset tool name %s, two distinct "
                    "actions resolve to the same wire name",
                    tool.name,
                )
                continue
            seen.add(tool.name)
            unique_ad_tools.append(tool)
        all_ad_tools = unique_ad_tools

        if not all_ad_tools:
            return 0

        model_id = resolve_model(ModelRole.PRIMARY, self.settings)
        context_window = get_context_window(model_id)

        tool_dicts = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in all_ad_tools
        ]
        relevance = {
            t.name: (1.0 if t.is_read_only({}) else 0.5)
            + 1.0 / (1 + len(json.dumps(t.input_schema, default=str)) / 1000.0)
            for t in all_ad_tools
        }
        loaded_dicts, deferred_dicts = filter_ad_tools_by_budget(
            tool_dicts, context_window, relevance_scores=relevance
        )
        loaded_names = {d["name"] for d in loaded_dicts}

        all_ad_tools.sort(key=lambda t: t.name)

        ad_tool_count = 0
        for tool in all_ad_tools:
            if self.ensure_tools().get_tool(tool.name) is not None:
                continue
            ad_source = (
                ToolSource.SANDBOXED
                if (self.settings and getattr(self.settings, "asset_directory_sandboxed", True))
                else ToolSource.MCP
            )
            tool.source = ad_source
            if tool.name in loaded_names:
                self.ensure_tools().register(tool)
            else:
                self.deferred_ad_tools[tool.name] = tool
                real_handler = tool.handler

                async def deferred_proxy(params: dict, handler=real_handler) -> str:
                    return await handler(params)

                self.ensure_tools().register(
                    ToolDef(
                        name=tool.name,
                        description=tool.description,
                        input_schema={"type": "object", "properties": {}},
                        handler=deferred_proxy,
                        source=ad_source,
                        defer_loading=True,
                        server_name=tool.server_name,
                        action_name=tool.action_name,
                        connection_id=tool.connection_id,
                    )
                )
            ad_tool_count += 1

        if self.deferred_ad_tools:
            logger.info(
                "AD tools: %d loaded, %d deferred (discoverable via tool_search)",
                len(loaded_names),
                len(self.deferred_ad_tools),
            )

        return ad_tool_count

    async def execute(
        self,
        user_input: str,
        thread_id: str | None = None,
        options: RequestOptions | None = None,
        ingestion_ids: list[str] | None = None,
        prefetch_memory_task: "asyncio.Task | None" = None,
    ) -> str:
        """Layer 2 — called per message. Orchestration + resilience.

        Acquires turn lock, loads history, refreshes search context,
        builds system prompt, calls agent_loop(), saves state.
        """
        tid = thread_id or self.current_thread_id
        self.current_thread_id = tid

        try:
            await asyncio.wait_for(self.turn_lock.acquire(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error("Turn lock timeout for thread %s", tid)
            raise TimeoutError(f"Thread {tid} turn lock timeout — another turn is still executing")

        from src.herald.usage_tracker import UsageAccumulator, usage_tracker_var

        tracker = UsageAccumulator()
        tracker_token = usage_tracker_var.set(tracker)
        try:
            self.sub_agent_results = []
            self.replan_state = {"triggered": False}
            self.denial_tracker.reset()

            if not self.session_started and self.session_start_hooks:
                self.session_started = True
                await self.fire_session_hooks(self.session_start_hooks)

            if self.lifecycle_hooks.has_hooks(HookEvent.USER_PROMPT_SUBMIT):
                from src.engine_core.lifecycle_hooks import UserPromptSubmitInput

                ups_reg = self.lifecycle_hooks.registry(HookEvent.USER_PROMPT_SUBMIT)
                ups_inp = UserPromptSubmitInput(
                    thread_id=tid,
                    text=user_input,
                    turn_number=0,
                )
                ups_agg = await ups_reg.execute_all(ups_inp)
                if ups_agg.prevent_continuation:
                    reason = (
                        ups_agg.blocking_errors[0]
                        if ups_agg.blocking_errors
                        else "user prompt blocked by hook"
                    )
                    return f"[blocked by hook] {reason}"
                if ups_agg.updated_payload and isinstance(ups_agg.updated_payload.get("text"), str):
                    user_input = ups_agg.updated_payload["text"]

            await self.set_state(self.ThreadState.EXECUTING, tid)
            await self.emitter.emit(ThreadLifecycle(thread_id=tid, status="started"))

            try:
                await self.memory.write(
                    tid,
                    EntryType.SYSTEM_EVENT,
                    json.dumps(
                        {
                            "user_input": user_input[:2000],
                        }
                    ),
                )
            except Exception as e:
                logger.debug("WM write (execution start) failed: %s", e)

            self.usage_tracker = tracker
            return await self.execute_turn(
                user_input,
                tid,
                options=options,
                ingestion_ids=ingestion_ids,
                prefetch_memory_task=prefetch_memory_task,
            )
        finally:
            usage_tracker_var.reset(tracker_token)
            self.turn_lock.release()

    async def emit_new_sources(self, registry) -> None:
        """Emit a SourceDiscovered for each registry record not yet emitted this
        turn (idempotent — safe to call after each feeder). Folds to a "source"
        block, gated by Gates.sources. No-op when the registry is None."""
        if registry is None:
            return
        tid = getattr(self, "current_thread_id", "")
        for handle, rec in list(registry.records.items()):
            if handle in registry.emitted:
                continue
            registry.emitted.add(handle)
            await self.emitter.emit(
                SourceDiscovered(
                    thread_id=tid,
                    handle=handle,
                    kind=rec.kind,
                    data=dict(rec.data),
                    turn_id=registry.turn_id,
                )
            )

    async def stream_text(self, text, cite_parser, registry, thread_id) -> str:
        """Emit a chunk of visible text and return it for history.

        The citation stage went with engine_core/citation_parser.py in the
        Kasukabe build: there is no ⟦cite:h⟧ marker to strip, so the model's
        text reaches the stream and the history byte-for-byte. The
        ``cite_parser``/``registry`` parameters are retained (always None) so
        every call site keeps its shape.
        """
        if text:
            await self.emitter.emit(TextDelta(thread_id=thread_id, text=text))
        return text

    async def stream_visible(
        self, text, vm_parser, vm_state, cite_parser, registry, thread_id
    ) -> str:
        """Emit one chunk of visible text. The voice/media stage went with
        engine_core/voice_parser.py; ``vm_parser``/``vm_state`` are always None
        here and the call is a straight pass-through to ``stream_text``."""
        return await self.stream_text(text, cite_parser, registry, thread_id)

    async def execute_turn(
        self,
        user_input: str,
        thread_id: str,
        options: RequestOptions | None = None,
        ingestion_ids: list[str] | None = None,
        prefetch_memory_task: "asyncio.Task | None" = None,
    ) -> str:
        """Inner turn execution — called with turn lock held."""
        if self.consciousness_mgr:
            refreshed = await self.consciousness_mgr.get()
            if refreshed is not None and refreshed is not self.consciousness:
                self.consciousness = refreshed
                logger.info("Consciousness updated (cache was invalidated by data change)")

        if self.consciousness_mgr and not self.consciousness_mgr.sections:
            self.register_prompt_sections()

        await self.refresh_connected_services_snapshot()

        sys_prompt = None
        if self.consciousness_mgr:
            sys_prompt = self.consciousness_mgr.build_prompt_blocks()

        from src.engine_core.coordinator import craft_gate_var

        if craft_gate_var.get():
            from src.herald.types import SystemPromptBlock

            sys_prompt = [
                *(sys_prompt or []),
                SystemPromptBlock(text=compose_craft_guidance()),
            ]

        from src.engine_core.coordinator import citations_gate_var

        if citations_gate_var.get():
            from src.herald.types import SystemPromptBlock

            sys_prompt = [
                *(sys_prompt or []),
                SystemPromptBlock(text=compose_citation_guidance()),
            ]


        reminder_parts: list[str] = []

        query_emb: list[float] | None = None
        prefetched: dict | None = None
        pending_recall_task: "asyncio.Task | None" = None

        if prefetch_memory_task is not None:
            if not prefetch_memory_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(prefetch_memory_task),
                        timeout=PREFETCH_BOUNDED_WAIT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    logger.debug("Prefetch wait failed: %s", e)
            if prefetch_memory_task.done():
                try:
                    prefetched = prefetch_memory_task.result()
                    merge_prefetch_usage(prefetched)
                except Exception as e:
                    logger.debug("Prefetch result failed: %s", e)
            else:
                pending_recall_task = prefetch_memory_task
        elif self.search:
            try:
                query_emb = await self.search.embed(user_input)
                episodes, knowledge, social = await asyncio.gather(
                    self.search.episodes(user_input, limit=10, query_embedding=query_emb),
                    self.search.knowledge(user_input, limit=10, query_embedding=query_emb),
                    self.search.social(user_input, limit=5, query_embedding=query_emb),
                )
                prefetched = {
                    "query_embedding": query_emb,
                    "episodes": episodes,
                    "knowledge": knowledge,
                    "social": social,
                }
            except Exception as e:
                logger.debug("Search context enrichment failed: %s", e)

        if prefetched is not None:
            query_emb = prefetched.get("query_embedding")
            from src.engine_core.coordinator import source_registry_var

            src_reg = source_registry_var.get()
            recall_block = build_recall_reminder(prefetched, registry=src_reg)
            if recall_block:
                reminder_parts.append(recall_block)
            await self.emit_new_sources(src_reg)

        if self.skill_catalog:
            try:
                matched_skills = self.skill_catalog.match_skills(user_input, query_emb, top_k=5)
                if matched_skills:
                    if self.compactor:
                        self.compactor.record_matched_skills(matched_skills)
                    skill_context = self.build_skill_context(matched_skills)
                    if skill_context:
                        reminder_parts.append(f"<system-reminder>{skill_context}</system-reminder>")
            except Exception as e:
                logger.debug("Skill discovery failed: %s", e)

        if complexity_score(user_input) >= COMPLEXITY_NUDGE_THRESHOLD:
            reminder_parts.append(
                "<system-reminder>This task appears complex. Consider calling request_plan "
                "to create a structured approach before proceeding.</system-reminder>"
            )

        if reminder_parts:
            enriched_input = "\n".join(reminder_parts) + "\n\n" + user_input
        else:
            enriched_input = user_input

        et = trace_mod()
        run_trace = et.NodeTrace(
            node_id="run",
            agent_name="run",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self.current_node_trace = run_trace
        run_start_ns = time.monotonic_ns()

        prior_messages: list = []
        conv_seq: int | None = 0
        from src.herald.types import Message as LLMMessage

        def parse_iso_to_epoch(iso_str: str | None) -> float | None:
            """Parse a SQLite ISO timestamp to a Unix float. Returns None on failure.

            SQLite ``datetime('now')`` writes ``YYYY-MM-DD HH:MM:SS`` (no tz) as UTC.
            We treat those as UTC wall-clock and convert. Callers use the float
            as the ``Message.timestamp`` on resumed messages so the time-based
            microcompact trigger still sees a real age.
            """
            if not iso_str:
                return None
            try:
                s = iso_str.replace(" ", "T")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except (ValueError, TypeError):
                return None

        if self.conversation_store is not None:
            try:
                await self.conversation_store.ensure_conversation(thread_id)
                stored = await self.conversation_store.load_messages(thread_id)
                for msg in stored:
                    prior_messages.append(
                        LLMMessage(
                            role=msg["role"],
                            content=msg["content"],
                            timestamp=parse_iso_to_epoch(msg.get("created_at")),
                        )
                    )
            except Exception as e:
                logger.debug("SQLite conversation load failed, falling back to WM: %s", e)
                prior_messages = []

            try:
                conv_seq = await self.conversation_store.next_seq(thread_id)
            except ConversationPersistenceError as e:
                logger.critical("Cannot determine conversation sequence for %s: %s", thread_id, e)
                conv_seq = None

        if not prior_messages:
            try:
                log_entries = await self.memory.read(thread_id)
                for entry in log_entries:
                    entry_ts = parse_iso_to_epoch(getattr(entry, "timestamp", None))
                    if entry.type == EntryType.MESSAGE_USER:
                        prior_messages.append(
                            LLMMessage(
                                role="user",
                                content=entry.content,
                                timestamp=entry_ts,
                            )
                        )
                    elif entry.type == EntryType.MESSAGE_ASSISTANT:
                        prior_messages.append(
                            LLMMessage(
                                role="assistant",
                                content=entry.content,
                                timestamp=entry_ts,
                            )
                        )
                if (
                    prior_messages
                    and prior_messages[-1].role == "user"
                    and prior_messages[-1].content == user_input
                ):
                    prior_messages.pop()
            except Exception as e:
                logger.debug("Failed to load conversation history: %s", e)

        from src.herald.catalog import get_context_window

        model_id = resolve_model(ModelRole.PRIMARY, self.settings)
        context_window = get_context_window(model_id)

        loop_state: LoopState | None = None
        if self.tools and self.tools.tool_count > 0:
            loop_state = await self.agent_loop(
                enriched_input,
                node_id="",
                system_prompt=sys_prompt,
                replan_state=self.replan_state,
                prior_messages=prior_messages,
                context_window=context_window,
                options=options,
                pending_recall_task=pending_recall_task,
            )
            full_text = loop_state.full_text
        else:
            from src.herald.types import Message as LLMMessage
            from src.engine_core.coordinator import citations_gate_var, source_registry_var

            all_messages = prior_messages + [
                LLMMessage(
                    role="user",
                    content=enriched_input,
                    timestamp=time.time(),
                )
            ]
            full_text = ""
            async for chunk in self.adapter.stream(all_messages, system=sys_prompt):
                full_text += await self.stream_text(chunk, None, None, thread_id)

        run_trace.completed_at = datetime.now(timezone.utc).isoformat()
        run_trace.duration_ms = (time.monotonic_ns() - run_start_ns) // 1_000_000
        run_trace.agent_output = full_text[:2000]
        if loop_state and loop_state.chain:
            run_trace.chain_id = loop_state.chain.chain_id
            run_trace.depth = loop_state.chain.depth
        self.execution_trace.nodes.append(run_trace)
        self.execution_trace.thread_id = thread_id
        self.execution_trace.total_duration_ms = run_trace.duration_ms

        try:
            trace_json = json.dumps(self.execution_trace.to_dict())
            await self.memory.write(thread_id, EntryType.SYSTEM_EVENT, trace_json)
        except Exception as e:
            logger.debug("WM write (execution trace) failed: %s", e)

        await self.memory.write(thread_id, EntryType.MESSAGE_ASSISTANT, full_text)

        if self.conversation_store is not None:
            if conv_seq is None:
                raise ConversationPersistenceError(
                    f"conversation sequence unavailable for {thread_id}; "
                    "refusing to write history at a guessed seq"
                )
            await self.conversation_store.save_message(
                thread_id,
                conv_seq,
                "user",
                json.dumps(user_input),
            )
            await self.conversation_store.save_message(
                thread_id,
                conv_seq + 1,
                "assistant",
                json.dumps(full_text),
            )

        await self.set_state(self.ThreadState.COMPLETED, thread_id)
        await self.emitter.emit(ThreadLifecycle(thread_id=thread_id, status="completed"))

        model_id = resolve_model(ModelRole.PRIMARY, self.settings)
        tracker = getattr(self, "usage_tracker", None)
        if tracker is not None:
            tracked = tracker.total()
            input_tok = tracked.input_tokens
            output_tok = tracked.output_tokens
            cache_read = tracked.cache_read_input_tokens
            cache_create = tracked.cache_creation_input_tokens
        elif loop_state is not None:
            input_tok = loop_state.input_tokens
            output_tok = loop_state.output_tokens
            cache_read = loop_state.cache_read
            cache_create = loop_state.cache_create
        else:
            input_tok = 0
            output_tok = 0
            cache_read = 0
            cache_create = 0
        cost = self.estimate_running_cost() or 0.0
        exit_reason = loop_state.exit_reason.value if loop_state else "completed"
        last_stop = loop_state.last_stop if loop_state else StopReason.END_TURN
        num_turns = loop_state.turns if loop_state else 0
        chain = loop_state.chain if loop_state else None
        cf = chain.event_fields() if chain else {}
        quota = tracker.latest_quota() if tracker is not None else None
        await self.emitter.emit(
            ExecutionSummary(
                thread_id=thread_id,
                status=exit_reason,
                duration_ms=run_trace.duration_ms,
                total_input_tokens=input_tok,
                total_output_tokens=output_tok,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                estimated_cost_usd=cost,
                tool_calls_count=len(run_trace.tool_calls)
                if hasattr(run_trace, "tool_calls")
                else 0,
                model_id=model_id,
                exit_reason=exit_reason,
                last_model_stop=str(last_stop),
                num_turns=num_turns,
                chain_id=cf.get("chain_id", ""),
                depth=cf.get("depth", 0),
                turn_id=cf.get("turn_id", ""),
                turn_counter=cf.get("turn_counter", 0),
                quota=quota,
            )
        )

        try:
            await self.memory.write(
                thread_id,
                EntryType.SYSTEM_EVENT,
                json.dumps(
                    {
                        "output_length": len(full_text),
                        "duration_ms": run_trace.duration_ms,
                    }
                ),
            )
        except Exception as e:
            logger.debug("WM write (execution done) failed: %s", e)

        return full_text

    def estimate_running_cost(self) -> float | None:
        """Return estimated USD cost so far, or None if unknown model."""
        from src.herald.catalog import estimate_cost

        model_id = resolve_model(ModelRole.PRIMARY, self.settings)
        tracker = getattr(self, "usage_tracker", None)
        if tracker:
            t = tracker.total()
            return estimate_cost(
                model_id,
                t.input_tokens,
                t.output_tokens,
                cache_read_tokens=t.cache_read_input_tokens,
                cache_create_tokens=t.cache_creation_input_tokens,
            )
        return None


    async def agent_loop(
        self,
        user_input: str,
        node_id: str = "",
        adapter_override: LLM | None = None,
        system_prompt: "list[SystemPromptBlock] | None" = None,
        replan_state: dict | None = None,
        prior_messages: list | None = None,
        context_window: int = 200_000,
        options: "RequestOptions | None" = None,
        pending_recall_task: "asyncio.Task | None" = None,
    ) -> LoopState:
        """Core agent loop: model → tool_call? → execute → result → model.

        Exits when: model finishes (end_turn), model refuses (refusal),
        output truncated (max_tokens), turn limit hit, budget exceeded,
        or external cancel.

        Returns LoopState with all accumulated metrics, messages, and exit info.
        system_prompt: structured prompt blocks with per-section cache scopes.
        options: RequestOptions with max_turns, max_budget_usd, super_thinking.
        prior_messages: conversation history loaded from the temporal log.
        """
        from src.herald.types import Message as LLMMessage, TextBlock, ToolUseBlock, ThinkingBlock
        from src.herald.types import ContentBlock
        from src.herald.types import Usage
        from src.engine_core.context_engineering import (
            generate_tombstone_results,
        )
        from src.herald.token_budget_parser import parse_token_budget
        from src.engine_core.token_budget import (
            create_budget_tracker,
            check_token_budget,
            ContinueDecision,
            pct_of_budget,
        )
        from src.engine_core.events import (
            CacheHitRatioAlert,
            TokenBudgetProgress,
            TokenBudgetContinuation,
            TokenBudgetCompleted,
        )
        from src.engine_core.cache_hit_monitor import CacheHitMonitor

        if adapter_override is None and options is not None and options.model:
            if True:
                try:
                    from src.herald import create_llm

                    adapter_override = create_llm(self.settings, options.model)
                    logger.info(
                        "Explicit model override active: %s (router bypassed)",
                        options.model,
                    )
                    try:
                        from src.raven.events import RouteDecisionEvent
                        from src.raven.api import fire_and_forget

                        fire_and_forget(
                            RouteDecisionEvent(
                                chosen_model=options.model,
                                reason="explicit-override (router inactive)",
                                routing_active=False,
                            )
                        )
                    except Exception:
                        pass
                except Exception as ov_err:
                    logger.warning(
                        "Explicit model override %s failed to build adapter (%s); "
                        "falling back to router.",
                        options.model,
                        ov_err,
                    )
            else:
                logger.warning(
                    "Requested model '%s' is not available; falling back to the default router.",
                    options.model,
                )

        adapter = adapter_override or self.adapter
        await self.ensure_compactor()
        self.compactor.update_context(charter=self.charter)
        messages: list[LLMMessage] = [
            *(prior_messages or []),
            LLMMessage(role="user", content=user_input, timestamp=time.time()),
        ]
        tools_schema = self.tools.to_tool_defs() if self.tools else []
        thread_id = getattr(self, "current_thread_id", "")

        max_turns_val = options.max_turns if options and options.max_turns else MAX_AGENT_TURNS
        if options and options.permission_mode:
            try:
                self.permission_mode = PermissionMode(options.permission_mode)
            except ValueError:
                logger.warning(
                    "Invalid permission_mode '%s', using DEFAULT", options.permission_mode
                )
                self.permission_mode = PermissionMode.DEFAULT
        state = LoopState(max_turns=max_turns_val, thread_id=thread_id)

        budget_enabled = bool(self.settings and self.settings.token_budget_enabled)
        if budget_enabled:
            parsed_budget = parse_token_budget(user_input)
            api_budget = options.token_budget if options else None
            effective_budget = parsed_budget or api_budget or self.last_turn_budget
            state.snapshot_turn_budget(effective_budget)
            if state.budget_tracker is None:
                state.budget_tracker = create_budget_tracker()
            self.last_turn_budget = effective_budget

        cache_monitor_enabled = bool(self.settings and self.settings.cache_hit_monitor_enabled)
        cache_monitor = CacheHitMonitor() if cache_monitor_enabled else None
        cache_monitor_last_ms: float | None = None

        chain_enabled = bool(self.settings and self.settings.query_chain_tracking_enabled)
        if chain_enabled:
            from src.engine_core.schema import create_root_chain

            query_src_init = options.query_source if options else "foreground"
            state.chain = create_root_chain(query_src_init)
        self.current_chain = state.chain
        self.current_node_id = node_id

        MAX_CONTINUATIONS = 3
        continuation_count = 0
        escalation_attempted = False
        MAX_MEDIA_CORRECTION_ROUNDS = 2
        media_correction_rounds = 0
        max_output_override: int | None = None
        stop_hook_active = False
        from src.config import Settings
        from src.herald.catalog import get_model

        model_max = None
        model_id = resolve_model(ModelRole.PRIMARY, self.settings)
        resolved = get_model(model_id)
        if resolved is not None:
            model_max = resolved.max_output_tokens
        ESCALATED_MAX_OUTPUT_TOKENS = min(64_000, model_max) if model_max else 64_000

        from src.herald.retry import (
            OverloadTracker,
            FallbackTriggeredError,
            HEARTBEAT_INTERVAL_S,
            is_context_overflow,
            get_retry_after,
            OVERLOAD_STATUS,
            PROMPT_CACHE_RETRY_CEILING,
        )
        from src.herald import create_llm
        from src.engine_core.events import ModelFallback, PersistentRetryWait

        query_src = options.query_source if options else "foreground"
        overload_tracker = OverloadTracker(
            original_model=model_id,
            fallback_model=resolve_model(ModelRole.FALLBACK, self.settings) or None,
            query_source=query_src,
            persistent=self.settings.persistent_retry if self.settings else False,
        )

        escalation_eligible = (
            self.settings is not None
            and self.settings.max_tokens == Settings.max_tokens
            and ESCALATED_MAX_OUTPUT_TOKENS > self.settings.max_tokens
        )

        try:
            se = await self.memory.read_by_type(thread_id, EntryType.SYSTEM_EVENT)
            for entry in reversed(se):
                if entry.content.startswith('{"_struggle":'):
                    state.struggle = StrugglingState.from_dict(
                        json.loads(entry.content)["_struggle"]
                    )
                    break
        except Exception:
            pass

        max_budget = options.max_budget_usd if options else None
        has_budget = max_budget is not None and max_budget > 0

        interrupt_source = get_interrupt_source()
        registered_executor: StreamingToolExecutor | None = None

        def register_turn_executor(ex: StreamingToolExecutor) -> None:
            nonlocal registered_executor
            if registered_executor is not None and registered_executor is not ex:
                interrupt_source.unregister(registered_executor)
                registered_executor.cleanup()
            interrupt_source.register(ex)
            registered_executor = ex

        def release_turn_executor() -> None:
            nonlocal registered_executor
            if registered_executor is not None:
                interrupt_source.unregister(registered_executor)
                registered_executor.cleanup()
                registered_executor = None

        async def run_stop_hooks(trigger: str, last_text: str, last_stop: str):
            flag_on = bool(self.settings and getattr(self.settings, "stop_hooks_enabled", True))
            if not flag_on:
                return None
            if not self.stop_hooks.has_hooks():
                return None
            try:
                sh_cwd = os.getcwd()
            except OSError:
                sh_cwd = ""
            inp = StopHookInput(
                stop_hook_active=stop_hook_active,
                last_assistant_message=last_text,
                turn_number=state.turns,
                last_stop_reason=last_stop,
                thread_id=thread_id,
                cwd=sh_cwd,
                hook_event_name="Stop",
            )
            agg = await self.stop_hooks.execute_all(inp)
            try:
                await self.emitter.emit(
                    StopHookExecuted(
                        thread_id=thread_id,
                        turn_number=state.turns,
                        trigger=trigger,
                        prevented=agg.prevent_continuation,
                        blocked=bool(agg.blocking_errors),
                        retry_active=stop_hook_active,
                        hook_outcomes=list(agg.hook_outcomes),
                    )
                )
            except Exception as e:  # noqa: BLE001 — observer must not kill loop
                logger.debug("StopHookExecuted emit failed: %s", e)
            return agg

        recall_consumed = False

        def make_recall_message(content: str) -> "LLMMessage":
            return LLMMessage(role="user", content=content, timestamp=time.time())

        for turn in range(state.max_turns):
            from src.engine_core.coordinator import source_registry_var

            src_reg = source_registry_var.get()
            was_consumed = recall_consumed
            messages, recall_consumed = consume_pending_recall(
                messages,
                pending_recall_task,
                recall_consumed,
                message_factory=make_recall_message,
                registry=src_reg,
            )
            if recall_consumed and not was_consumed:
                await self.emit_new_sources(src_reg)

            if self.compactor is not None:
                try:
                    await self.compactor.reset_for_new_user_message(thread_id)
                except Exception:
                    logger.debug("reset_for_new_user_message failed (non-fatal)")

            if state.chain:
                state.chain.tick_turn()

            try:
                from src.raven.events import AgentStepEvent
                from src.raven.api import fire_and_forget

                fire_and_forget(
                    AgentStepEvent(
                        step_type="turn",
                        step_id=f"turn_{turn}_{thread_id}",
                        parent_id=thread_id,
                    )
                )
            except Exception:
                pass

            if self.cancel_requested:
                tombstones = generate_tombstone_results(messages, "[Cancelled by user]")
                messages = [*messages, *tombstones]
                state.exit_reason = LoopExitReason.CANCELLED
                state.full_text += "\n[Thread terminated by external command]"
                break

            if has_budget and state.turns > 0:
                assert max_budget is not None
                cost = self.estimate_running_cost()
                if cost is not None:
                    from src.herald.catalog import format_usd

                    remaining = max_budget - cost
                    messages = [
                        *messages,
                        LLMMessage(
                            role="user",
                            content=(
                                f"<system-reminder>Budget: {format_usd(cost)} used "
                                f"of {format_usd(max_budget)} ({format_usd(remaining)} remaining)"
                                f"</system-reminder>"
                            ),
                        ),
                    ]

            if (
                budget_enabled
                and state.turns > 0
                and state.current_turn_token_budget
                and state.current_turn_token_budget > 0
            ):
                tb_used = state.get_turn_output_tokens()
                tb_budget = state.current_turn_token_budget
                tb_pct = pct_of_budget(tb_used, tb_budget)
                tb_cont = state.budget_tracker.continuation_count if state.budget_tracker else 0
                messages = [
                    *messages,
                    LLMMessage(
                        role="user",
                        content=(
                            f"<system-reminder>Token target: {tb_used:,} / "
                            f"{tb_budget:,} used this turn ({tb_pct}%, "
                            f"{tb_cont} continuation(s) so far). Keep working "
                            f"productively toward the target."
                            f"</system-reminder>"
                        ),
                        metadata={"is_meta": True},
                    ),
                ]

            struggle_reason = state.struggle.check(has_charter=self.charter is not None)
            if struggle_reason and not state.struggle.struggle_triggered:
                state.struggle.struggle_triggered = True
                action = "auto_replan" if self.charter else "auto_plan"

                await self.emitter.emit(
                    StrugglingDetected(
                        thread_id=thread_id,
                        reason=struggle_reason,
                        action_taken=action,
                        turn_count=state.turns,
                        consecutive_errors=state.struggle.consecutive_errors,
                        turns_without_text=state.struggle.turns_without_text,
                        turns_since_checkpoint=state.struggle.turns_since_checkpoint,
                        repeated_pattern=struggle_reason if "repeated" in struggle_reason else "",
                    )
                )

                replan_tool = self.ensure_tools().get_tool("request_replan") if self.tools else None
                plan_tool = self.ensure_tools().get_tool("request_plan") if self.tools else None
                if self.charter and replan_tool is not None:
                    try:
                        await replan_tool.handler(
                            {
                                "reason": f"Auto-replan: {struggle_reason}",
                            }
                        )
                    except Exception as e:
                        logger.debug("Auto-replan failed: %s", e)
                elif plan_tool is not None:
                    try:
                        await plan_tool.handler(
                            {
                                "query": user_input,
                                "context_summary": f"Struggling after {state.turns} turns: {struggle_reason}",
                            }
                        )
                    except Exception as e:
                        logger.debug("Auto-plan failed: %s", e)

                messages = [
                    *messages,
                    LLMMessage(
                        role="user",
                        content=f"[System: Execution was struggling ({struggle_reason}). "
                        f"A {'replan' if self.charter else 'plan'} has been triggered. "
                        f"Follow the updated goals.]",
                    ),
                ]

                state.struggle.reset()
                try:
                    await self.memory.write(
                        thread_id,
                        EntryType.SYSTEM_EVENT,
                        json.dumps({"_struggle": state.struggle.to_dict()}),
                    )
                except Exception:
                    pass

            chain_turn_id_before = state.chain.turn_id if state.chain else ""
            compact_result = await self.compactor.compact_if_needed(
                messages,
                system_prompt,
                tools_schema,
                thread_id,
                state.turns,
                context_window,
                query_source=query_src,
                chain_id=state.chain.chain_id if state.chain else "",
                turn_id=chain_turn_id_before,
                chain_ref=state.chain,
            )
            messages = compact_result.messages

            if cache_monitor is not None and (
                compact_result.was_compacted or compact_result.microcompacted
            ):
                cache_monitor.suppress_next_drop()

            if compact_result.hook_contexts:
                from src.herald.types import Message as HookMessage

                hook_reminder = "\n\n".join(
                    f"<system-reminder>{ctx}</system-reminder>"
                    for ctx in compact_result.hook_contexts
                    if ctx
                )
                if hook_reminder:
                    messages = list(messages) + [
                        HookMessage(
                            role="user",
                            content=hook_reminder,
                            timestamp=time.time(),
                        )
                    ]

            if budget_enabled and compact_result.was_compacted and state.current_turn_token_budget:
                state.snapshot_turn_budget(state.current_turn_token_budget)
                assert state.budget_tracker is not None
                state.budget_tracker.reset_delta_origin()

            messages_for_api = list(messages)
            turn_assistant_msgs: list[LLMMessage] = []
            turn_tool_results_msgs: list[LLMMessage] = []

            turn_text = ""
            turn_tool_calls: list[LLMToolCall] = []
            turn_thinking: list[ThinkingBlock] = []
            turn_stop_reason = StopReason.END_TURN
            turn_usage = Usage()
            turn_message_id = ""
            tid = getattr(self, "current_thread_id", "")

            turn_batch_id = (
                f"{state.chain.chain_id}-{str(uuid.uuid4())[:8]}"
                if state.chain
                else str(uuid.uuid4())[:8]
            )
            turn_assistant_id = str(uuid.uuid4()) if state.chain else ""

            def check_concurrency(tc: LLMToolCall) -> bool:
                if not self.tools:
                    return False
                batches = partition_tool_calls([tc], self.tools)
                return batches[0].is_concurrency_safe if batches else False

            def check_interrupt(tc: LLMToolCall) -> InterruptBehavior:
                td = self.ensure_tools().get_tool(tc.name) if self.tools else None
                return td.get_interrupt_behavior(tc.input) if td else InterruptBehavior.BLOCK

            def check_command_execution(tc: LLMToolCall) -> bool:
                td = self.ensure_tools().get_tool(tc.name) if self.tools else None
                return bool(td and td.is_command_execution)

            def check_tool_exists(name: str) -> bool:
                return (self.ensure_tools().get_tool(name) is not None) if self.tools else True

            def make_executor(cancel_token: CancellationToken) -> StreamingToolExecutor:
                return StreamingToolExecutor(
                    execute_fn=lambda call, ct: self.run_single_tool(
                        call,
                        ct,
                        batch_id=turn_batch_id,
                        node_id=node_id,
                        thread_id=tid,
                        source_assistant_id=turn_assistant_id,
                    ),
                    concurrency_check_fn=check_concurrency,
                    interrupt_behavior_fn=check_interrupt,
                    is_command_execution_fn=check_command_execution,
                    tool_exists_fn=check_tool_exists,
                    parent_cancellation=cancel_token,
                    emitter=self.emitter,
                    thread_id=tid,
                )

            def make_and_register(cancel_token: CancellationToken) -> StreamingToolExecutor:
                ex = make_executor(cancel_token)
                register_turn_executor(ex)
                return ex

            turn_cancel = CancellationToken()
            turn_executor = make_and_register(turn_cancel)

            MAX_OVERFLOW_RETRIES = 3
            fallback_triggered = False
            from src.engine_core.coordinator import (
                craft_gate_var,
                craft_edit_var,
                craft_wait_var,
                citations_gate_var,
                source_registry_var,
            )

            craft_on = craft_gate_var.get()
            craft_wait = craft_wait_var.get()
            craft_edit = craft_edit_var.get()
            citations_on = citations_gate_var.get()
            src_reg = source_registry_var.get()
            for overflow_attempt in range(MAX_OVERFLOW_RETRIES + 1):
                turn_text = ""
                halt_for_wait = False
                turn_tool_calls = []
                turn_thinking = []
                mid_stream_results: list[ToolResultBlock] = []
                craft_parser = None
                vm_parser = None
                vm_state: dict = {"voice_open": False, "rejections": []}
                cite_parser = None
                turn_stop_reason = StopReason.END_TURN
                turn_usage = Usage()

                if overflow_attempt > 0:
                    turn_executor.discard()
                    turn_cancel = CancellationToken()
                    turn_executor = make_and_register(turn_cancel)

                try:
                    super_thinking_flag = options.super_thinking if options else False
                    async for event in adapter.stream_with_tools(
                        messages_for_api,
                        system=system_prompt,
                        tools=tools_schema,
                        super_thinking=super_thinking_flag,
                        max_output_tokens=max_output_override,
                    ):
                        if event.type == "text" and event.text:
                            turn_text += await self.stream_visible(
                                event.text, vm_parser, vm_state, cite_parser, src_reg, tid
                            )
                        elif event.type == "tool_call" and event.tool_call:
                            turn_tool_calls.append(event.tool_call)
                            turn_executor.add_tool(event.tool_call)
                            if not turn_cancel.cancelled:
                                for mid in turn_executor.get_completed_results():
                                    mid_stream_results.append(mid)
                        elif event.type == "thinking" and event.text:
                            await self.emitter.emit(ThinkingDelta(thread_id=tid, text=event.text))
                        elif event.type == "thinking_block" and event.thinking_block:
                            turn_thinking.append(event.thinking_block)
                        elif event.type == "stop" and event.stop_reason is not None:
                            turn_stop_reason = event.stop_reason
                            turn_message_id = event.message_id
                        elif event.type == "usage" and event.usage is not None:
                            turn_usage = event.usage
                    break
                except Exception as exc:
                    try:
                        exc_code = overload_tracker.record_error(exc)
                    except FallbackTriggeredError as fb_err:
                        logger.warning(
                            "Model fallback: %s -> %s after %d consecutive 529s",
                            fb_err.original_model,
                            fb_err.fallback_model,
                            fb_err.consecutive_count,
                        )
                        adapter = create_llm(self.settings, fb_err.fallback_model)
                        messages = strip_thinking_blocks(messages)
                        tombstones = generate_tombstone_results(
                            messages,
                            f"[Model fallback: {fb_err.original_model} -> {fb_err.fallback_model}]",
                        )
                        messages = [*messages, *tombstones]
                        turn_executor.discard()
                        turn_cancel = CancellationToken()
                        turn_executor = make_and_register(turn_cancel)
                        overload_tracker = OverloadTracker(
                            original_model=fb_err.fallback_model,
                            fallback_model=None,
                            query_source=query_src,
                        )
                        await self.emitter.emit(
                            ModelFallback(
                                thread_id=tid,
                                original_model=fb_err.original_model,
                                fallback_model=fb_err.fallback_model,
                                consecutive_529s=fb_err.consecutive_count,
                            )
                        )
                        state.last_transition_reason = (
                            f"model_fallback:{fb_err.original_model}->{fb_err.fallback_model}"
                        )
                        fallback_triggered = True
                        break

                    if exc_code == OVERLOAD_STATUS:
                        ra = get_retry_after(exc)
                        if ra is not None and ra < PROMPT_CACHE_RETRY_CEILING:
                            logger.info(
                                "529 with retry-after=%.1fs, retrying same model to preserve cache",
                                ra,
                            )
                            state.last_transition_reason = f"529_retry_after:{ra:.1f}s"
                            await asyncio.sleep(ra)
                            continue

                    if not is_context_overflow(exc) or overflow_attempt >= MAX_OVERFLOW_RETRIES:
                        advice = overload_tracker.get_persistent_delay(exc, exc_code)
                        if advice is not None:
                            remaining = advice.delay_s
                            while remaining > 0:
                                await self.emitter.emit(
                                    PersistentRetryWait(
                                        thread_id=tid,
                                        status_code=exc_code or 0,
                                        remaining_s=remaining,
                                        attempt=advice.attempt,
                                    )
                                )
                                chunk = min(remaining, HEARTBEAT_INTERVAL_S)
                                await asyncio.sleep(chunk)
                                remaining -= chunk
                            state.last_transition_reason = (
                                f"persistent_retry:attempt={advice.attempt}"
                            )
                            continue
                        from src.herald.provider import (
                            CitadelBudgetExhausted,
                            CitadelQuotaExhausted,
                            CitadelSubscriptionSuspended,
                        )

                        if not isinstance(
                            exc,
                            (
                                CitadelQuotaExhausted,
                                CitadelBudgetExhausted,
                                CitadelSubscriptionSuspended,
                            ),
                        ):
                            err_msgs = [
                                *messages_for_api,
                                *turn_assistant_msgs,
                                *turn_tool_results_msgs,
                            ]
                            err_tombstones = generate_tombstone_results(
                                err_msgs,
                                f"[Runtime error: {exc}]",
                            )
                            messages = [*messages, *err_tombstones]
                        raise

                    if not (self.settings and self.settings.reactive_compact_enabled):
                        state.last_transition_reason = "reactive_compact_disabled"
                        raise

                    for tier in range(overflow_attempt, MAX_OVERFLOW_RETRIES + 1):
                        logger.warning(
                            "Context overflow (tier %d/%d), compacting...",
                            tier,
                            MAX_OVERFLOW_RETRIES,
                        )
                        rc_result = await self.compactor.reactive_compact(
                            messages,
                            tier,
                            context_window,
                            thread_id=tid,
                            turn_number=state.turns,
                            query_source=query_src,
                            chain_id=state.chain.chain_id if state.chain else "",
                            turn_id=state.chain.turn_id if state.chain else "",
                        )
                        if rc_result.made_progress:
                            break
                        logger.warning(
                            "Reactive compact tier %d (%s) made no progress "
                            "(%d tokens unchanged), escalating to next tier",
                            rc_result.tier,
                            rc_result.strategy,
                            rc_result.tokens_before,
                        )

                    if not rc_result.made_progress:
                        state.last_transition_reason = (
                            f"reactive_compact_exhausted:last_tier={rc_result.tier}"
                        )
                        from src.errors import EngineError, ErrorCode

                        raise EngineError(
                            code=ErrorCode.CONTEXT_OVERFLOW_UNRECOVERABLE,
                            message=(
                                "Context overflow unrecoverable: all tiers exhausted, no progress"
                            ),
                            details={
                                "tier": rc_result.tier,
                                "strategy": rc_result.strategy,
                                "tokens_before": rc_result.tokens_before,
                                "tokens_after": rc_result.tokens_after,
                            },
                        )

                    state.last_transition_reason = (
                        f"reactive_compact:tier={rc_result.tier},strategy={rc_result.strategy}"
                    )
                    messages = rc_result.messages
                    if cache_monitor is not None:
                        cache_monitor.suppress_next_drop()
                    if state.chain:
                        state.chain.on_compaction()
                    messages_for_api = list(messages)
                    turn_assistant_msgs = []
                    turn_tool_results_msgs = []

            if fallback_triggered:
                continue

            overload_tracker.record_success()
            state.record_turn_usage(turn_usage)

            if cache_monitor is not None:
                now_ms = time.monotonic() * 1000
                cm_elapsed_ms = (
                    int(now_ms - cache_monitor_last_ms)
                    if cache_monitor_last_ms is not None
                    else None
                )
                cache_monitor_last_ms = now_ms
                for alert in cache_monitor.observe_turn(
                    turn=state.turns,
                    cache_read=getattr(turn_usage, "cache_read_input_tokens", 0),
                    cache_create=getattr(turn_usage, "cache_creation_input_tokens", 0),
                    uncached=turn_usage.input_tokens,
                    model=getattr(adapter, "model", ""),
                    tool_count=len(tools_schema) if tools_schema else 0,
                    elapsed_ms=cm_elapsed_ms,
                ):
                    logger.warning("cache_hit_monitor: %s", alert.message)
                    await self.emitter.emit(
                        CacheHitRatioAlert(
                            thread_id=tid,
                            alert_type=alert.alert_type.value,
                            turn=alert.turn,
                            cache_ratio=alert.cache_ratio,
                            cache_read=alert.cache_read,
                            cache_create=alert.cache_create,
                            uncached=alert.uncached,
                            message=alert.message,
                            prev_cache_read=alert.prev_cache_read,
                            drop_pct=alert.drop_pct,
                        )
                    )

            if budget_enabled and state.current_turn_token_budget:
                tb_turn_tokens = state.get_turn_output_tokens()
                await self.emitter.emit(
                    TokenBudgetProgress(
                        thread_id=tid,
                        turn_tokens=tb_turn_tokens,
                        budget=state.current_turn_token_budget,
                        pct=pct_of_budget(tb_turn_tokens, state.current_turn_token_budget),
                        continuation_count=(
                            state.budget_tracker.continuation_count if state.budget_tracker else 0
                        ),
                    )
                )

            if has_budget:
                assert max_budget is not None
                running_cost = self.estimate_running_cost()
                if running_cost is not None and running_cost >= max_budget:
                    from src.herald.catalog import format_usd

                    state.exit_reason = LoopExitReason.BUDGET_EXCEEDED
                    if turn_text:
                        state.full_text += turn_text
                    state.full_text += f"\n\n[Budget limit of {format_usd(max_budget)} reached. Session cost: {format_usd(running_cost)}]"
                    logger.warning(
                        "Budget exceeded: %s / %s", format_usd(running_cost), format_usd(max_budget)
                    )
                    break

            if turn_text:
                state.full_text += turn_text
                try:
                    if tid:
                        await self.memory.write(tid, EntryType.SYSTEM_EVENT, turn_text[:2000])
                except Exception as e:
                    logger.debug("WM write (text delta) failed: %s", e)

            if halt_for_wait:
                turn_executor.discard()
                turn_tool_calls = []
                turn_stop_reason = StopReason.END_TURN

            if turn_stop_reason != StopReason.TOOL_USE or not turn_tool_calls:
                if turn_stop_reason == StopReason.MAX_TOKENS:
                    turn_executor.discard()
                    if turn_tool_calls:
                        tombstones = generate_tombstone_results(
                            [*messages_for_api, *turn_assistant_msgs],
                            "[Tool execution interrupted — output token limit reached]",
                        )
                        messages = [*messages, *tombstones]
                    turn_tool_calls = []

                    if not escalation_attempted and escalation_eligible:
                        escalation_attempted = True
                        max_output_override = ESCALATED_MAX_OUTPUT_TOKENS
                        logger.info(
                            "MAX_TOKENS escalation: retrying with max_output_tokens=%d",
                            ESCALATED_MAX_OUTPUT_TOKENS,
                        )
                        continue
                    escalation_attempted = True

                    max_output_override = None
                    if continuation_count < MAX_CONTINUATIONS:
                        now_ts = time.time()
                        messages = [
                            *messages_for_api,
                            *turn_assistant_msgs,
                            LLMMessage(
                                role="assistant",
                                content=turn_text or "",
                                timestamp=now_ts,
                            ),
                            LLMMessage(
                                role="user",
                                content=(
                                    "Output token limit hit. Resume directly — no apology, "
                                    "no recap of what you were doing. Pick up mid-thought if "
                                    "that is where the cut happened. Break remaining work into "
                                    "smaller pieces."
                                ),
                                timestamp=now_ts,
                            ),
                        ]

                        continuation_count += 1

                        logger.info(
                            "MAX_TOKENS continuation %d/%d (output_tokens=%d)",
                            continuation_count,
                            MAX_CONTINUATIONS,
                            turn_usage.output_tokens,
                        )
                        continue

                    logger.info("MAX_TOKENS recovery exhausted (%d attempts)", continuation_count)

                if (
                    budget_enabled
                    and turn_stop_reason != StopReason.MAX_TOKENS
                    and state.current_turn_token_budget
                    and state.budget_tracker is not None
                ):
                    tb_decision = check_token_budget(
                        tracker=state.budget_tracker,
                        is_sub_agent=False,
                        budget=state.current_turn_token_budget,
                        global_turn_tokens=state.get_turn_output_tokens(),
                    )
                    if isinstance(tb_decision, ContinueDecision):
                        await self.emitter.emit(
                            TokenBudgetContinuation(
                                thread_id=tid,
                                continuation_count=tb_decision.continuation_count,
                                pct=tb_decision.pct,
                                turn_tokens=tb_decision.turn_tokens,
                                budget=tb_decision.budget,
                                nudge_message=tb_decision.nudge_message,
                            )
                        )
                        logger.info(
                            "Token budget continuation %d at %d%% (%d / %d)",
                            tb_decision.continuation_count,
                            tb_decision.pct,
                            tb_decision.turn_tokens,
                            tb_decision.budget,
                        )
                        messages = [
                            *messages_for_api,
                            *turn_assistant_msgs,
                            LLMMessage(
                                role="user",
                                content=tb_decision.nudge_message,
                                metadata={"is_meta": True},
                            ),
                        ]
                        state.snapshot_turn_budget(state.current_turn_token_budget)
                        continue
                    elif tb_decision.completion_event is not None:
                        ev = tb_decision.completion_event
                        await self.emitter.emit(
                            TokenBudgetCompleted(
                                thread_id=tid,
                                continuation_count=ev.continuation_count,
                                pct=ev.pct,
                                turn_tokens=ev.turn_tokens,
                                budget=ev.budget,
                                diminishing_returns=ev.diminishing_returns,
                                duration_ms=ev.duration_ms,
                            )
                        )
                        if ev.diminishing_returns:
                            logger.info(
                                "Token budget early-stop: diminishing returns at %d%% "
                                "(turn %d, %d tokens)",
                                ev.pct,
                                state.turns,
                                ev.turn_tokens,
                            )

                state.last_stop = turn_stop_reason
                if turn_stop_reason == StopReason.REFUSAL:
                    state.exit_reason = LoopExitReason.REFUSAL
                elif turn_stop_reason == StopReason.MAX_TOKENS:
                    state.exit_reason = LoopExitReason.MAX_TOKENS

                if self.signal_store is not None and len(user_input.strip()) >= 20:
                    try:
                        from src.learning_centre.signals import LearningSignal

                        tid = getattr(self, "current_thread_id", "")

                        summary_prompt = (
                            "Summarize this exchange in under 100 tokens total. "
                            "Format: [user] what they said/asked (50 tok max) "
                            "[engine] what you did/responded (50 tok max). "
                            "Focus on facts stated, decisions made, people mentioned, "
                            "and actions taken. Omit pleasantries."
                        )
                        try:
                            summary = await adapter.generate(
                                [
                                    *messages_for_api,
                                    *turn_assistant_msgs,
                                    *turn_tool_results_msgs,
                                    LLMMessage(
                                        role="user", content=summary_prompt, timestamp=time.time()
                                    ),
                                ],
                                system=system_prompt,
                            )
                        except Exception:
                            summary = f"[user] {user_input[:100]} [engine] {state.full_text[:100]}"

                        tool_ctx = ""
                        for tc in turn_tool_calls:
                            if tc.name == "ask_user" or tc.name.startswith("ad:"):
                                result_str = str(getattr(tc, "result", "") or "")[:200]
                                tool_ctx += f" [tool:{tc.name}] {result_str}"
                        tool_ctx = tool_ctx[:800]

                        signal_content = summary[:300] + tool_ctx

                        await self.signal_store.write(
                            LearningSignal(
                                type="event",
                                source="user",
                                content=signal_content,
                                subject=user_input[:40],
                                thread_id=tid,
                            )
                        )
                    except Exception as e:
                        logger.debug("Post-turn signal capture failed: %s", e)

                sh_agg = await run_stop_hooks(
                    "after_end_turn",
                    turn_text,
                    turn_stop_reason.value if turn_stop_reason else "",
                )
                if sh_agg is not None:
                    if sh_agg.prevent_continuation:
                        state.exit_reason = LoopExitReason.HOOK_PREVENTED
                        sh_notice = format_hook_prevent_notice(sh_agg)
                        state.full_text += sh_notice
                        break
                    if sh_agg.blocking_errors and not stop_hook_active:
                        sh_reminder = (
                            "<system-reminder>\n"
                            + "\n".join(sh_agg.blocking_errors)
                            + "\n</system-reminder>"
                        )
                        messages = [
                            *messages,
                            LLMMessage(
                                role="user",
                                content=sh_reminder,
                                metadata={"is_meta": True},
                                timestamp=time.time(),
                            ),
                        ]
                        stop_hook_active = True
                        if state.exit_reason in (
                            LoopExitReason.REFUSAL,
                            LoopExitReason.MAX_TOKENS,
                        ):
                            state.exit_reason = LoopExitReason.COMPLETED
                        continue

                break

            calls = turn_tool_calls

            tool_results: list[ToolResultBlock] = list(mid_stream_results)
            async for result in turn_executor.results():
                tool_results.append(result)

            tool_names = {c.id: c.name for c in calls}
            tool_results = cap_turn_tool_results(
                tool_results, context_window, tool_names=tool_names
            )

            asyncio.ensure_future(
                generate_tool_use_summary(
                    tool_calls=calls,
                    tool_results=tool_results,
                    last_assistant_text=turn_text,
                    settings=self.settings,
                    emitter=self.emitter,
                    thread_id=tid,
                    turn_number=state.turns,
                    cancellation_token=turn_cancel,
                )
            )

            assistant_blocks: list[ContentBlock] = []
            for tb in turn_thinking:
                assistant_blocks.append(tb)
            if turn_text:
                assistant_blocks.append(TextBlock(text=turn_text))
            for call in turn_tool_calls:
                assistant_blocks.append(
                    ToolUseBlock(
                        id=call.id,
                        name=call.name,
                        input=call.input,
                    )
                )
            turn_now_ts = time.time()
            turn_assistant_msgs.append(
                LLMMessage(
                    role="assistant",
                    content=assistant_blocks,
                    timestamp=turn_now_ts,
                    api_message_id=turn_message_id or None,
                )
            )
            turn_tool_results_msgs.append(
                LLMMessage(
                    role="user",
                    content=cast("list[ContentBlock]", tool_results),
                    timestamp=turn_now_ts,
                )
            )
            messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]

            escalation_attempted = False
            max_output_override = None
            continuation_count = 0

            sh_agg_tool = await run_stop_hooks(
                "after_tool_use",
                turn_text,
                turn_stop_reason.value if turn_stop_reason else "",
            )
            if sh_agg_tool is not None:
                if sh_agg_tool.prevent_continuation:
                    state.exit_reason = LoopExitReason.HOOK_PREVENTED
                    state.full_text += format_hook_prevent_notice(sh_agg_tool)
                    break
                if sh_agg_tool.blocking_errors and not stop_hook_active:
                    sh_reminder_tool = (
                        "<system-reminder>\n"
                        + "\n".join(sh_agg_tool.blocking_errors)
                        + "\n</system-reminder>"
                    )
                    messages = [
                        *messages,
                        LLMMessage(
                            role="user",
                            content=sh_reminder_tool,
                            metadata={"is_meta": True},
                            timestamp=time.time(),
                        ),
                    ]
                    stop_hook_active = True
                else:
                    stop_hook_active = False

            state.struggle.record_tool_results(
                tool_calls=turn_tool_calls,
                tool_results=tool_results,
                had_text=bool(turn_text.strip()),
                had_checkpoint=any(c.name == "report_checkpoint" for c in turn_tool_calls),
            )

            try:
                await self.memory.write(
                    thread_id,
                    EntryType.SYSTEM_EVENT,
                    json.dumps({"_struggle": state.struggle.to_dict()}),
                )
            except Exception:
                pass

            if replan_state and replan_state.get("new_charter"):
                new_charter = replan_state.pop("new_charter")
                new_tools = replan_state.pop("new_tools", None)
                self.charter = new_charter
                if new_tools is not None:
                    deferred_tool_names = [
                        meta["name"]
                        for meta in new_tools.list_tools_meta()
                        if self.ensure_tools().get_tool(meta["name"]) is None
                    ]
                    if deferred_tool_names:
                        logger.info(
                            "Replan deferred %d new tools to next session: %s",
                            len(deferred_tool_names),
                            deferred_tool_names,
                        )
                mission_reminder = self.build_mission_reminder(new_charter)
                self.last_injected_charter = new_charter
                messages = [
                    *messages,
                    LLMMessage(
                        role="user",
                        content=f"<system-reminder>{mission_reminder}</system-reminder>",
                    ),
                ]

                replan_items = [
                    {"node_id": n.node_id, "label": n.label, "status": "pending"}
                    for n in new_charter.nodes
                ]
                await self.emitter.emit(
                    NodeProgress(
                        thread_id=getattr(new_charter, "thread_id", ""),
                        charter_id=getattr(new_charter, "charter_id", ""),
                        status="replanned",
                        is_replan=True,
                        items=replan_items,
                    )
                )

                try:
                    replan_tid = getattr(new_charter, "thread_id", self.current_thread_id)
                    if replan_tid:
                        await self.memory.write(
                            replan_tid,
                            EntryType.SYSTEM_EVENT,
                            json.dumps(
                                {
                                    "event": "replan",
                                    "items": replan_items,
                                }
                            ),
                        )
                except Exception as e:
                    logger.debug("WM write (todo replan) failed: %s", e)

                try:
                    await self.memory.write(
                        getattr(new_charter, "thread_id", self.current_thread_id),
                        EntryType.SYSTEM_EVENT,
                        json.dumps(
                            {
                                "event": "charter_update",
                                "new_nodes": [n.node_id for n in new_charter.nodes],
                                "charter_version": getattr(new_charter, "charter_version", 0),
                            }
                        ),
                    )
                except Exception as e:
                    logger.debug("WM write (charter update) failed: %s", e)

            if (
                self.charter is not None
                and self.charter is not self.session_charter
                and self.charter is not self.last_injected_charter
            ):
                self.last_injected_charter = self.charter
                is_initial = self.session_charter is None
                mission_reminder = self.build_mission_reminder(self.charter, is_initial=is_initial)
                messages = [
                    *messages,
                    LLMMessage(
                        role="user",
                        content=f"<system-reminder>{mission_reminder}</system-reminder>",
                    ),
                ]

                trace_watermarks: dict = {
                    "tool_calls": 0,
                    "skill_calls": 0,
                    "asset_calls": 0,
                    "ask_user": 0,
                    "last_checkpoint_at": datetime.now(timezone.utc).isoformat(),
                }
                self.register_checkpoint_tool(self.charter, trace_watermarks)

                todo_items = [
                    {"node_id": n.node_id, "label": n.label, "status": "pending"}
                    for n in self.charter.nodes
                ]
                await self.emitter.emit(
                    NodeProgress(
                        thread_id=getattr(self, "current_thread_id", ""),
                        charter_id=self.charter.charter_id,
                        items=todo_items,
                    )
                )

        else:
            if state.exit_reason == LoopExitReason.COMPLETED:
                state.exit_reason = LoopExitReason.MAX_TURNS

        if self.current_node_trace is not None:
            et = trace_mod()
            self.current_node_trace.agent_loop = et.AgentLoopTrace(
                turns=state.turns,
                total_input_tokens=state.input_tokens,
                total_output_tokens=state.output_tokens,
                goal=user_input[:200],
            )

        release_turn_executor()

        return state

    async def run_single_tool(
        self,
        call: "LLMToolCall",
        cancellation_token: CancellationToken,
        batch_id: str = "",
        node_id: str = "",
        thread_id: str = "",
        source_assistant_id: str = "",
    ) -> "ToolResultBlock":
        """Execute one tool call with permission checks, tracing, and events.

        Extracted from the former nested function in agent_loop so that
        StreamingToolExecutor can call it via execute_fn callback.
        """
        from src.herald.types import ToolResultBlock

        chain_ref = getattr(self, "current_chain", None)

        if cancellation_token.cancelled:
            return ToolResultBlock(
                tool_use_id=call.id,
                content=f"Cancelled: {cancellation_token.reason}",
                is_error=True,
            )

        is_ask_user = call.name == "ask_user"
        tool_start_ns = time.monotonic_ns()

        if is_ask_user:
            question = call.input.get("question", "")
            try:
                if thread_id:
                    await self.memory.write(
                        thread_id,
                        EntryType.USER_INPUT_REQUEST,
                        json.dumps(
                            {
                                "question": question,
                                "node_id": node_id,
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("WM write (ask-user request) failed: %s", e)
        else:
            source = tool_source(call.name)
            await self.emitter.emit(
                ToolExecution(
                    thread_id=thread_id,
                    tool_name=call.name,
                    source=source,
                    phase="call",
                    params=call.input,
                    parallel_batch_id=batch_id,
                    chain_id=chain_ref.chain_id if chain_ref else "",
                )
            )
            try:
                if thread_id:
                    await self.memory.write(
                        thread_id,
                        EntryType.TOOL_CALL,
                        json.dumps(
                            {
                                "tool": call.name,
                                "input": str(call.input)[:500],
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("WM write (tool call %s) failed: %s", call.name, e)

        effective_input = call.input
        pre_additional_contexts: list[str] = []
        pre_hooks_on = bool(
            self.settings and getattr(self.settings, "pre_tool_hooks_enabled", True)
        )
        if not is_ask_user and pre_hooks_on and self.pre_tool_hooks.has_hooks():
            pre_inp = PreToolUseHookInput(
                tool_name=call.name,
                tool_input=call.input,
                tool_use_id=call.id,
                thread_id=thread_id,
            )
            pre_agg = await self.pre_tool_hooks.execute_all(pre_inp)
            await self.emitter.emit(
                PreToolUseHookExecuted(
                    thread_id=thread_id,
                    tool_name=call.name,
                    tool_use_id=call.id,
                    denied=pre_agg.denied,
                    allowed=pre_agg.allowed,
                    prevented=pre_agg.prevent_continuation,
                    hook_outcomes=list(pre_agg.hook_outcomes),
                )
            )
            if pre_agg.prevent_continuation:
                self.tool_hook_prevented = True
                return ToolResultBlock(
                    tool_use_id=call.id,
                    content="[Pre-tool hook stopped execution]",
                )
            if pre_agg.denied:
                return ToolResultBlock(
                    tool_use_id=call.id,
                    content=(
                        f"Permission denied by hook: {pre_agg.deny_reason}"
                        if pre_agg.deny_reason
                        else "Permission denied by hook"
                    ),
                )
            if pre_agg.blocking_error:
                return ToolResultBlock(
                    tool_use_id=call.id,
                    content=pre_agg.blocking_error,
                )
            if pre_agg.updated_input is not None:
                effective_input = pre_agg.updated_input
            pre_additional_contexts = pre_agg.additional_contexts

        tool_def = self.ensure_tools().get_tool(call.name) if self.tools else None
        tool_source_val = tool_def.source if tool_def else ToolSource.PLATFORM
        perm = await check_permission(
            tool_name=call.name,
            tool_input=effective_input,
            source=tool_source_val,
            rules=self.current_permission_rules(),
            ask_user_fn=self.ask_user_callback,
            permission_request_fn=self.permission_request_hook_fn(),
            permission_mode=self.permission_mode,
        )
        if perm.new_rule is not None:
            if perm.persist and self.permission_store:
                self.permission_store.add_rule(perm.new_rule)
                self.permission_store.save(PermissionProvenance.USER)
            else:
                self.session_rules.append(perm.new_rule)
        if not perm.allowed:
            result_text = f"Permission denied: {perm.reason}"
            if self.lifecycle_hooks.has_hooks(HookEvent.PERMISSION_DENIED):
                from src.engine_core.lifecycle_hooks import PermissionDeniedInput

                pd_reg = self.lifecycle_hooks.registry(HookEvent.PERMISSION_DENIED)
                pd_inp = PermissionDeniedInput(
                    thread_id=getattr(self, "current_thread_id", ""),
                    tool_name=call.name,
                    tool_input=effective_input,
                    tool_use_id=call.id,
                    reason=perm.reason or "",
                )
                try:
                    pd_agg = await pd_reg.execute_all(pd_inp)
                    if pd_agg.additional_contexts:
                        extra = "\n\n".join(pd_agg.additional_contexts)
                        result_text = f"{result_text}\n\n{extra}"
                except Exception as exc:
                    logger.debug("PermissionDenied hook dispatch failed: %s", exc)
            denial = self.denial_tracker.record_denial(call.name)
            if denial is not None:
                result_text = f"{result_text}\n\n{denial.injection}"
                if self.ask_user_callback is None:
                    raise DenialLimitExceeded(
                        f"Agent aborted: {denial.total_count} "
                        f"denial(s) with no user approval channel"
                    )
                user_response = await self.await_user_input(
                    f"The agent has been denied {denial.total_count} "
                    f"time(s) (recent tools: {denial.recent_tools}). "
                    "How should it proceed?"
                )
                result_text = f"{result_text}\n\nUser guidance: {user_response}"
                if denial.hit_total_limit:
                    self.denial_tracker.reset_total()
            tool_duration_ms = (time.monotonic_ns() - tool_start_ns) // 1_000_000
            if self.current_node_trace is not None:
                self.current_node_trace.record_tool_call(
                    call.name,
                    input=effective_input,
                    output=result_text,
                    duration_ms=tool_duration_ms,
                    success=False,
                    call_id=call.id,
                    tool_def=self.tools.get_tool(call.name),
                )
            return ToolResultBlock(tool_use_id=call.id, content=result_text)

        result_text = await self.execute_tool(call.name, effective_input)
        self.denial_tracker.record_success()
        tool_duration_ms = (time.monotonic_ns() - tool_start_ns) // 1_000_000
        tool_success = not result_text.startswith("Error")

        if tool_success and call.name in ("file_read", "read_file") and self.compactor:
            file_path = effective_input.get("file_path") or effective_input.get("path", "")
            if file_path:
                import hashlib

                try:
                    with open(file_path, "r", errors="replace") as f:
                        raw = f.read()
                    content_hash = hashlib.sha256(raw.encode(errors="replace")).hexdigest()
                    raw_size = len(raw)
                except (OSError, IOError):
                    content_hash = ""
                    raw_size = 0
                self.compactor.record_file_read(
                    file_path,
                    content_hash=content_hash,
                    size=raw_size,
                )

        effective_result = result_text
        post_additional_contexts: list[str] = []
        post_hooks_on = bool(
            self.settings and getattr(self.settings, "post_tool_hooks_enabled", True)
        )
        if post_hooks_on and self.post_tool_hooks.has_hooks():
            post_ctx = PostToolHookContext(
                tool_name=call.name,
                tool_input=effective_input,
                result_text=result_text,
                call_id=call.id,
                batch_id=batch_id,
                thread_id=thread_id,
                permission_feedback=perm.feedback,
                cancelled=cancellation_token.cancelled,
            )
            post_agg = await self.post_tool_hooks.execute_all(post_ctx)
            try:
                await self.emitter.emit(
                    PostToolUseHookExecuted(
                        thread_id=thread_id,
                        tool_name=call.name,
                        tool_use_id=call.id,
                        prevented=post_agg.prevent_continuation,
                        context_count=len(post_agg.additional_contexts),
                        hook_outcomes=list(post_agg.hook_outcomes),
                    )
                )
            except Exception as e:
                logger.debug("PostToolUseHookExecuted emit failed: %s", e)
            if post_agg.prevent_continuation:
                self.tool_hook_prevented = True
            if post_agg.updated_tool_output is not None:
                effective_result = post_agg.updated_tool_output
            post_additional_contexts = post_agg.additional_contexts

        if not tool_success and self.lifecycle_hooks.has_hooks(HookEvent.POST_TOOL_USE_FAILURE):
            from src.engine_core.lifecycle_hooks import PostToolUseFailureInput

            ptf_reg = self.lifecycle_hooks.registry(HookEvent.POST_TOOL_USE_FAILURE)
            ptf_inp = PostToolUseFailureInput(
                thread_id=thread_id,
                tool_name=call.name,
                tool_input=effective_input,
                tool_use_id=call.id,
                error_text=result_text,
            )
            try:
                ptf_agg = await ptf_reg.execute_all(ptf_inp)
                if ptf_agg.additional_contexts:
                    post_additional_contexts = list(post_additional_contexts) + list(
                        ptf_agg.additional_contexts
                    )
            except Exception as exc:
                logger.debug("PostToolUseFailure hook dispatch failed: %s", exc)

        from src.engine_core.coordinator import source_registry_var

        src_reg = source_registry_var.get()
        if src_reg is not None:
            if tool_success:
                effective_result = register_tool_source(call.name, effective_result, src_reg)
            await self.emit_new_sources(src_reg)

        builder = ToolResultBuilder(
            tool_use_id=call.id,
            base_result=effective_result,
            is_error=not tool_success,
            tool_name=call.name,
            source_assistant_id=source_assistant_id,
        )
        if perm.feedback:
            builder.append_text(perm.feedback, label="permission")
        for cb in perm.content_blocks:
            append_permission_content_block(builder, cb)
        for ctx_text in pre_additional_contexts:
            builder.append_text(ctx_text, label="hook")
        for ctx_text in post_additional_contexts:
            builder.append_text(ctx_text, label="hook")
        cap_builder_size(
            builder,
            tool_name=call.name,
            results_dir=lambda: self.results_dir,
        )

        if self.current_node_trace is not None:
            self.current_node_trace.record_tool_call(
                call.name,
                input=effective_input,
                output=result_text[:500] if call.name != "ask_user" else result_text,
                duration_ms=tool_duration_ms,
                success=tool_success,
                call_id=call.id,
                tool_def=self.tools.get_tool(call.name),
            )

        if is_ask_user:
            try:
                if thread_id:
                    await self.memory.write(
                        thread_id,
                        EntryType.USER_INPUT_RESPONSE,
                        json.dumps(
                            {
                                "question": call.input.get("question", ""),
                                "answer": result_text[:2000],
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("WM write (ask-user response) failed: %s", e)
        else:
            source = tool_source(call.name)
            await self.emitter.emit(
                ToolExecution(
                    thread_id=thread_id,
                    tool_name=call.name,
                    source=source,
                    phase="result",
                    result=result_text[:2000],
                    duration=tool_duration_ms,
                    status="success" if tool_success else "error",
                    parallel_batch_id=batch_id,
                    chain_id=chain_ref.chain_id if chain_ref else "",
                )
            )
            try:
                if thread_id:
                    await self.memory.write(
                        thread_id,
                        EntryType.TOOL_RESULT,
                        json.dumps(
                            {
                                "tool": call.name,
                                "result": result_text[:2000],
                                "duration_ms": tool_duration_ms,
                                "success": tool_success,
                            }
                        ),
                    )
            except Exception as e:
                logger.debug("WM write (tool result %s) failed: %s", call.name, e)

        return builder.build()

    async def execute_tool(self, name: str, input: dict[str, Any]) -> str:
        """Look up a tool by name and execute it. Returns the result string."""
        if not self.tools:
            return "Error: no tool registry available"
        return await self.tools.execute(name, input)
