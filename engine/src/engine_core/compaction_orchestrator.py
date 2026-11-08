"""Compaction Orchestrator — centralized context window compaction.

Owns all compaction decisions and execution. Replaces scattered inline
compaction logic in thread.py with a single coordinator that runs a
strategy pipeline, tracks history, and implements a circuit breaker.

Strategy pipeline (ordered by aggression):
  0. time-based microcompact — clear stale tool_result content on idle
  1. snip_prepass — remove provably redundant tool results (always, no LLM)
  2. context_collapse pre-pass — mechanical, incremental, risk-scored
     span projection. Owns the threshold ladder when enabled; returns
     early if projection relieves enough pressure. Falls through to the
     LLM strategies below only when everything is high-risk.
  3. session_memory_compact — use Working Memory summary instead of LLM call
  4. llm_compact — full LLM-based summarization via compact_messages_with_summary()
  5. mechanical_compact — compact_messages() gut+drop
  6. emergency_compact — keep first + last N, gut everything

Threshold triggers:
  - Every turn: snip_prepass, then context_collapse (if enabled)
  - 80% context: session_memory → llm_compact → mechanical fallback
  - Preflight over budget: emergency_compact
  - API overflow: context_collapse drain → reactive 3-tier chain
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from src.engine_core.context_collapse import ContextCollapseManager
    from src.engine_core.context_engineering import ContentReplacementState
    from src.engine_core.events import EventEmitter
    from src.engine_core.microcompact_config import MicrocompactConfig
    from src.engine_core.schema import Charter
    from src.infra.sqlite_pool import SqlitePool
    from src.herald import LLM
    from src.memory.memory_factory import WorkingMemoryBackend
    from src.engine_core.lifecycle_hooks import LifecycleHookBundle

from src.engine_core.context_collapse import STRATEGY_CONTEXT_COLLAPSE
from src.engine_core.hooks import HookEvent

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3
CIRCUIT_BREAKER_HALF_OPEN_SECONDS = 300
CIRCUIT_BREAKER_SHRINK_THRESHOLD = 0.30


class CircuitState(Enum):
    """State machine for the compaction circuit breaker.

    CLOSED     — normal operation; LLM compaction allowed.
    OPEN       — tripped; LLM compaction blocked; mechanical fallback only.
    HALF_OPEN  — cooldown elapsed; one probe attempt armed for the next turn.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


COMPACTION_BOUNDARY_CONTENT = "[Compaction boundary -- conversation summarized up to this point]"
POST_COMPACT_MAX_FILES = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
POST_COMPACT_TOOL_REMINDER_TOKEN_BUDGET = 2_000
POST_COMPACT_SKILL_TOKEN_BUDGET = 25_000
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
FILE_UNCHANGED_STUB = (
    "[File content unchanged since last read — refer to the earlier Read "
    "tool_result in this conversation.]"
)
TRUNCATION_MARKER = "\n[File truncated for post-compact restoration]"

FEATURE_POST_COMPACT_HARDENED = "FEATURE_POST_COMPACT_HARDENED"
FEATURE_STRUCTURED_FILE_EXTRACTION = "FEATURE_STRUCTURED_FILE_EXTRACTION"


def post_compact_hardened_enabled() -> bool:
    return os.environ.get(FEATURE_POST_COMPACT_HARDENED, "true").strip().lower() != "false"


def structured_extraction_enabled() -> bool:
    return os.environ.get(FEATURE_STRUCTURED_FILE_EXTRACTION, "true").strip().lower() != "false"


@dataclass
class CompactionConfig:
    """SQLite-persisted compaction thresholds. Loaded once per session."""

    min_tokens: int = 10_000
    min_text_block_messages: int = 5
    max_tokens: int = 40_000
    compaction_threshold: float = 0.80
    circuit_breaker_max_failures: int = 3
    circuit_breaker_half_open_seconds: int = CIRCUIT_BREAKER_HALF_OPEN_SECONDS
    circuit_breaker_shrink_threshold: float = CIRCUIT_BREAKER_SHRINK_THRESHOLD
    circuit_breaker_reset_per_user_message: bool = True

    @classmethod
    async def load(cls, db: "SqlitePool | None") -> CompactionConfig:
        """Load config from SQLite. Returns defaults if DB unavailable."""
        config = cls()
        if db is None:
            return config
        try:
            rows = await db.fetch("SELECT key, value FROM compaction_config")
            kv = {row["key"]: row["value"] for row in rows}
            if "min_tokens" in kv:
                config.min_tokens = int(kv["min_tokens"])
            if "min_text_block_messages" in kv:
                config.min_text_block_messages = int(kv["min_text_block_messages"])
            if "max_tokens" in kv:
                config.max_tokens = int(kv["max_tokens"])
            if "compaction_threshold" in kv:
                config.compaction_threshold = float(kv["compaction_threshold"])
            if "circuit_breaker_max_failures" in kv:
                config.circuit_breaker_max_failures = int(kv["circuit_breaker_max_failures"])
            if "circuit_breaker_half_open_seconds" in kv:
                config.circuit_breaker_half_open_seconds = int(
                    kv["circuit_breaker_half_open_seconds"]
                )
            if "circuit_breaker_shrink_threshold" in kv:
                config.circuit_breaker_shrink_threshold = float(
                    kv["circuit_breaker_shrink_threshold"]
                )
            if "circuit_breaker_reset_per_user_message" in kv:
                config.circuit_breaker_reset_per_user_message = (
                    kv["circuit_breaker_reset_per_user_message"].strip().lower() != "false"
                )
        except Exception:
            logger.debug("Failed to load compaction config from SQLite, using defaults")
        return config


@dataclass
class CompactionRecord:
    """Single compaction event for history tracking."""

    timestamp: float
    strategy: str
    tokens_before: int
    tokens_after: int
    tokens_freed: int
    messages_before: int
    messages_after: int
    success: bool
    turn_number: int


@dataclass
class CompactionResult:
    """Return value from compact_if_needed()."""

    messages: list
    strategy: str = "none"
    tokens_freed: int = 0
    was_compacted: bool = False
    hook_contexts: list[str] = field(default_factory=list)
    microcompacted: bool = False


@dataclass
class ReactiveCompactResult:
    """Return value from reactive_compact()."""

    messages: list
    strategy: str = "none"
    tokens_before: int = 0
    tokens_after: int = 0
    made_progress: bool = False
    tier: int = -1


@dataclass
class FileReadRecord:
    """Content-at-read-time storage for TOCTOU-safe post-compact restoration."""

    timestamp: float
    content_hash: str = ""
    size: int = 0


@dataclass
class PostCompactAttachment:
    """Typed wrapper for post-compact injected content."""

    type: str
    content: str
    path: str = ""
    tokens: int = 0
    truncated: bool = False


EXCLUDE_EXTENSIONS = frozenset({".key", ".pem", ".p12"})
EXCLUDE_DIR_SEGMENTS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        ".tox",
        "secrets",
        "credentials",
    }
)
EXCLUDE_SENSITIVE_BASENAMES = frozenset(
    {
        "credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "secrets.env",
        "secret",
        "secret.json",
        "secret.yaml",
        "secret.yml",
    }
)
EXCLUDE_EXACT_NAMES = frozenset({".env"})
SYSTEM_CONTEXT_DIR_SEGMENTS = frozenset({".claude"})
SYSTEM_CONTEXT_FILENAMES = frozenset({"claude.md", ".claudeignore", ".clauderc"})
READ_TOOL_NAMES = frozenset({"file_read", "read_file"})


def tokens_to_dense_chars(token_budget: int, *, marker_tokens: int = 0) -> int:
    """Invert estimate_tokens_dense to get a character limit for a token budget.

    estimate_tokens_dense(text) = len(text) / 2 * SAFETY_MARGIN
    Solving for len(text): chars = (token_budget - marker_tokens) / SAFETY_MARGIN * 2
    """
    from src.engine_core.context_engineering import SAFETY_MARGIN

    return int((token_budget - marker_tokens) / SAFETY_MARGIN * 2)


def should_exclude_from_restore(path: str, *, plan_file_path: str = "") -> bool:
    """Exclude sensitive, irrelevant, or system-context files from restoration."""
    lower = path.lower()
    parts = lower.replace("\\", "/").split("/")
    basename = parts[-1] if parts else ""

    if any(seg in EXCLUDE_DIR_SEGMENTS for seg in parts):
        return True
    if any(basename.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
        return True
    if basename in EXCLUDE_SENSITIVE_BASENAMES:
        return True
    if basename in EXCLUDE_EXACT_NAMES:
        return True
    if basename.startswith(".env.") and not basename.endswith(".example"):
        return True
    if any(seg in SYSTEM_CONTEXT_DIR_SEGMENTS for seg in parts):
        return True
    if basename in SYSTEM_CONTEXT_FILENAMES:
        return True
    if plan_file_path:
        try:
            if os.path.normpath(path) == os.path.normpath(plan_file_path):
                return True
        except (ValueError, OSError):
            pass

    return False


def read_file_sync(path: str) -> str:
    with open(path, "r", errors="replace") as f:
        return f.read()


def collect_file_paths_from_messages(messages: list) -> set[str]:
    """Extract file paths from preserved messages for deduplication.

    When hardened mode is enabled, walks structured ToolUseBlock instances
    and calls extract_file_path for precise matching. Falls back to regex
    on plain-text content for non-structured messages (e.g., restoration
    messages from prior compactions).

    Implements two-pass dedup-stub handling: Pass 1 collects tool_use_ids
    whose tool_result starts with FILE_UNCHANGED_STUB. Pass 2 walks Read
    tool_use blocks and skips those in the stub set — ensuring files whose
    original Read was compacted away get re-injected via restoration.
    """
    if post_compact_hardened_enabled() and structured_extraction_enabled():
        return collect_file_paths_structured(messages)
    return collect_file_paths_regex(messages)


def collect_file_paths_structured(messages: list) -> set[str]:
    """Structured tool-use extraction with dedup-stub awareness."""
    from src.herald.types import ToolUseBlock, ToolResultBlock
    from src.engine_core.context_engineering import FILE_PATH_RE

    paths: set[str] = set()

    stub_ids: set[str] = set()
    for msg in messages:
        if getattr(msg, "role", "") != "user":
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ToolResultBlock):
                result_text = getattr(block, "content", "")
                if isinstance(result_text, str) and result_text.startswith(FILE_UNCHANGED_STUB):
                    stub_ids.add(getattr(block, "tool_use_id", ""))

    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolUseBlock):
                    if block.name not in READ_TOOL_NAMES:
                        continue
                    tool_id = getattr(block, "id", "")
                    if tool_id in stub_ids:
                        continue
                    tool_input = getattr(block, "input", {}) or {}
                    p = tool_input.get("file_path") or tool_input.get("path")
                    if p:
                        paths.add(os.path.normpath(p))

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                str(getattr(b, "text", ""))
                for b in content
                if not isinstance(b, (ToolUseBlock, ToolResultBlock)) and hasattr(b, "text")
            )
        else:
            text = ""
        if text:
            for match in FILE_PATH_RE.finditer(text):
                p = match.group(1)
                if len(p) > 2:
                    paths.add(os.path.normpath(p))

    return paths


def collect_file_paths_regex(messages: list) -> set[str]:
    """Legacy regex-based file path extraction."""
    from src.engine_core.context_engineering import FILE_PATH_RE

    paths: set[str] = set()
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(str(getattr(b, "text", getattr(b, "content", ""))) for b in content)
        else:
            continue
        for match in FILE_PATH_RE.finditer(text):
            p = match.group(1)
            if len(p) > 2:
                paths.add(os.path.normpath(p))
    return paths


def extract_discovered_tool_names(messages: list) -> set[str]:
    """Extract unique tool names from ToolUseBlocks in the message history.

    Used to preserve which tools were discovered/loaded before compaction,
    so deferred tool schemas survive across the compaction boundary.
    """
    from src.herald.types import ToolUseBlock

    names: set[str] = set()
    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolUseBlock):
                    names.add(block.name)
    return names


class CompactionOrchestrator:
    """Centralized compaction coordinator for the agent loop.

    Stored on the Thread instance and reused across execute() calls so that
    circuit breaker state and compaction history persist across turns.
    Calls existing context_engineering functions as strategy implementations.
    """

    def __init__(
        self,
        adapter: LLM,
        emitter: EventEmitter,
        memory: WorkingMemoryBackend,
        *,
        consciousness_mgr: Any = None,
        charter: "Charter | None" = None,
        db: "SqlitePool | None" = None,
        config: CompactionConfig | None = None,
        microcompact_config: "MicrocompactConfig | None" = None,
        search: Any = None,
        utility: Any = None,
        assets: Any = None,
        collapse_manager: "ContextCollapseManager | None" = None,
        skill_catalog: Any = None,
        agent_id: str = "",
        lifecycle_bundle: "LifecycleHookBundle | None" = None,
    ) -> None:
        from src.engine_core.microcompact_config import MicrocompactConfig

        self.adapter = adapter
        self.emitter = emitter
        self.memory = memory
        self.consciousness_mgr = consciousness_mgr
        self.charter = charter
        self.db = db
        self.config = config or CompactionConfig()
        self.microcompact_config = microcompact_config or MicrocompactConfig()
        self.search = search
        self.utility = utility
        self.assets = assets
        self.collapse_manager = collapse_manager
        self.skill_catalog = skill_catalog
        self.agent_id = agent_id
        self.lifecycle_bundle = lifecycle_bundle
        self.consecutive_failures = 0
        self.circuit_state: CircuitState = CircuitState.CLOSED
        self.tripped_at: float | None = None
        self.last_message_count_at_trip: int = 0
        self.last_token_count_at_trip: int = 0
        self.last_trip_error: str = ""
        self.total_trips: int = 0
        self.total_probes: int = 0
        self.total_probe_successes: int = 0
        self.history: list[CompactionRecord] = []
        self.file_read_state: dict[str, FileReadRecord] = {}
        self.last_matched_skills: list[dict] = []
        self.last_summarized_msg_index: int | None = None
        self.replacement_state_cache: "ContentReplacementState | None" = None


    @property
    def circuit_breaker_tripped(self) -> bool:
        """True when breaker is OPEN or HALF_OPEN (i.e. not fully closed)."""
        return self.circuit_state in (CircuitState.OPEN, CircuitState.HALF_OPEN)

    @circuit_breaker_tripped.setter
    def circuit_breaker_tripped(self, value: bool) -> None:
        """Shim for legacy assignment: True → OPEN, False → CLOSED."""
        if value:
            if self.circuit_state == CircuitState.CLOSED:
                self.circuit_state = CircuitState.OPEN
        else:
            self.circuit_state = CircuitState.CLOSED

    @property
    def half_open_probe_active(self) -> bool:
        return self.circuit_state == CircuitState.HALF_OPEN

    @half_open_probe_active.setter
    def half_open_probe_active(self, value: bool) -> None:
        if value:
            self.circuit_state = CircuitState.HALF_OPEN
        elif self.circuit_state == CircuitState.HALF_OPEN:
            self.circuit_state = CircuitState.OPEN

    @property
    def replacement_state(self) -> "ContentReplacementState":
        if self.replacement_state_cache is None:
            from src.engine_core.context_engineering import ContentReplacementState

            self.replacement_state_cache = ContentReplacementState()
        return self.replacement_state_cache

    def update_context(self, *, charter: "Charter | None" = None) -> None:
        """Update mutable references that change between execute() calls."""
        self.charter = charter

    def record_matched_skills(self, skills: list[dict]) -> None:
        """Record skills matched for the current turn, for post-compact reinjection."""
        if skills:
            self.last_matched_skills = list(skills)

    def record_file_read(
        self,
        path: str,
        *,
        content_hash: str = "",
        size: int = 0,
    ) -> None:
        """Record a file read with timestamp and content fingerprint.

        content_hash and size enable TOCTOU-safe restoration: if the file
        has not changed since the read, restoration can confidently annotate
        the content as current. If the hash differs, the restoration message
        notes that the file was modified since last read.
        """
        if path:
            self.file_read_state[path] = FileReadRecord(
                timestamp=time.time(),
                content_hash=content_hash,
                size=size,
            )


    async def trip_breaker(
        self,
        thread_id: str,
        *,
        error: str = "",
        message_count: int = 0,
        token_count: int = 0,
        reason: str = "",
    ) -> None:
        """Transition breaker to OPEN and emit CompactionCircuitBreakerTripped."""
        from src.engine_core.events import CompactionCircuitBreakerTripped

        self.circuit_state = CircuitState.OPEN
        self.tripped_at = time.time()
        self.last_trip_error = error
        self.last_message_count_at_trip = message_count
        self.last_token_count_at_trip = token_count
        self.total_trips += 1
        logger.warning(
            "Circuit breaker tripped after %d consecutive failures (trip #%d)",
            self.consecutive_failures,
            self.total_trips,
        )
        await self.emitter.emit(
            CompactionCircuitBreakerTripped(
                thread_id=thread_id,
                consecutive_failures=self.consecutive_failures,
                last_error=error,
                message_count=message_count,
                token_count=token_count,
                reason=reason or f"max_failures_{self.config.circuit_breaker_max_failures}",
            )
        )

    async def check_half_open_transition(self, thread_id: str) -> None:
        """Transition OPEN → HALF_OPEN if cooldown has elapsed; emit probe event."""
        from src.engine_core.events import CompactionCircuitBreakerProbeAttempt

        if self.circuit_state != CircuitState.OPEN or self.tripped_at is None:
            return
        elapsed = time.time() - self.tripped_at
        if elapsed < self.config.circuit_breaker_half_open_seconds:
            return
        self.circuit_state = CircuitState.HALF_OPEN
        self.total_probes += 1
        logger.info(
            "Circuit breaker HALF_OPEN — probe #%d armed (elapsed %.0fs)",
            self.total_probes,
            elapsed,
        )
        await self.emitter.emit(
            CompactionCircuitBreakerProbeAttempt(
                thread_id=thread_id,
                elapsed_seconds=elapsed,
                consecutive_failures=self.consecutive_failures,
                reason=f"cooldown_elapsed_{int(elapsed)}s",
            )
        )

    async def check_context_shrink_reset(
        self,
        thread_id: str,
        *,
        pre_tokens: int,
        post_tokens: int,
    ) -> None:
        """Reset CLOSED consecutive_failures when a mechanical pass frees enough tokens."""
        from src.engine_core.events import CompactionCircuitBreakerReset

        if self.circuit_state != CircuitState.CLOSED:
            return
        if pre_tokens <= 0 or self.consecutive_failures <= 0:
            return
        freed_fraction = (pre_tokens - post_tokens) / pre_tokens
        if freed_fraction < self.config.circuit_breaker_shrink_threshold:
            return
        self.consecutive_failures = 0
        logger.info(
            "Circuit breaker shrink-reset: mechanical freed %.0f%% of tokens",
            freed_fraction * 100,
        )
        await self.emitter.emit(
            CompactionCircuitBreakerReset(
                thread_id=thread_id,
                transition="reset_shrink",
                consecutive_failures=0,
                reason=f"mechanical_freed_{freed_fraction:.0%}",
            )
        )

    async def reset_breaker(self, thread_id: str, transition: str, reason: str) -> None:
        """Fully reset the breaker to CLOSED and emit CompactionCircuitBreakerReset."""
        from src.engine_core.events import CompactionCircuitBreakerReset

        self.consecutive_failures = 0
        self.circuit_state = CircuitState.CLOSED
        self.tripped_at = None
        if transition == "reset_success":
            self.total_probe_successes += 1
        logger.info("Circuit breaker reset: transition=%s reason=%s", transition, reason)
        await self.emitter.emit(
            CompactionCircuitBreakerReset(
                thread_id=thread_id,
                transition=transition,
                consecutive_failures=0,
                reason=reason,
            )
        )

    async def reset_for_new_user_message(self, thread_id: str = "") -> None:
        """Called at the top of every agent_loop iteration for a new user turn.

        When ``circuit_breaker_reset_per_user_message`` is True (default):
          - CLOSED with failures > 0: decrement consecutive_failures by 1 (decay).
          - OPEN: check if cooldown has elapsed and arm HALF_OPEN probe.

        This is the canonical entry point replacing on_user_message(); the old
        name is kept as an alias for backward compat.
        """
        if not self.config.circuit_breaker_reset_per_user_message:
            return

        from src.engine_core.events import CompactionCircuitBreakerReset

        if self.circuit_state == CircuitState.CLOSED and self.consecutive_failures > 0:
            self.consecutive_failures -= 1
            await self.emitter.emit(
                CompactionCircuitBreakerReset(
                    thread_id=thread_id,
                    transition="reset_decay",
                    consecutive_failures=self.consecutive_failures,
                    reason="per_user_message_decay",
                )
            )
        elif self.circuit_state == CircuitState.OPEN:
            await self.check_half_open_transition(thread_id)

    async def on_user_message(self, thread_id: str = "") -> None:
        """Backward-compat alias for reset_for_new_user_message()."""
        await self.reset_for_new_user_message(thread_id)

    async def manual_reset(self, thread_id: str = "") -> None:
        """Unconditionally reset the circuit breaker to CLOSED.

        Intended for operator intervention or test teardown. Emits a
        CompactionCircuitBreakerReset with transition='manual_reset'.
        """
        await self.reset_breaker(thread_id, "manual_reset", "operator_reset")

    async def compact_if_needed(
        self,
        messages: list,
        system_prompt: "list | None",
        tools_schema: list | None,
        thread_id: str,
        turn_number: int,
        context_window: int,
        *,
        query_source: str | None = None,
        chain_id: str = "",
        turn_id: str = "",
        chain_ref: "Any | None" = None,
    ) -> CompactionResult:
        """Run the compaction pipeline. Called before each LLM request.

        query_source flows through from the caller (typically ExecutionThread
        agent_loop, which extracts it from RequestOptions). It gates the
        cache-aware snip pre-pass below via is_main_thread_compact: the
        server-side cache_edits deletion path only runs for a main-thread
        compaction, so a subagent-flagged source falls back to the in-place
        snip_prepass. None is treated as main-thread.
        """
        from src.engine_core.context_engineering import (
            snip_prepass,
            needs_compaction,
            compact_messages_with_summary,
            compact_messages,
            guard_tool_results,
            emergency_compact,
            estimate_messages_tokens,
            estimate_tokens,
            estimate_system_tokens,
            RESERVE_FLOOR,
        )
        from src.engine_core.events import (
            CompactionEvent,
            MicrocompactEvent,
            CompactionCircuitBreakerSkip,
        )
        from src.engine_core.time_based_microcompact import (
            maybe_microcompact,
            TriggerMode,
        )

        compaction_reset_fired = False

        def fire_chain_reset() -> str:
            """Reset chain turn tracking on first compaction; return turn_id_after."""
            nonlocal compaction_reset_fired
            if chain_ref and not compaction_reset_fired:
                chain_ref.on_compaction()
                compaction_reset_fired = True
            return chain_ref.turn_id if chain_ref else turn_id

        if os.environ.get("DISABLE_COMPACT") or os.environ.get("DISABLE_AUTO_COMPACT"):
            return CompactionResult(messages=messages)

        mc_result = maybe_microcompact(
            messages,
            self.microcompact_config,
            TriggerMode.TIME_BASED,
        )
        microcompacted = mc_result is not None
        if mc_result is not None:
            messages = mc_result.messages
            if hasattr(self.adapter, "reset_snip_state"):
                self.adapter.reset_snip_state()
            await self.emitter.emit(
                MicrocompactEvent(
                    thread_id=thread_id,
                    mode=mc_result.mode.value,
                    gap_minutes=round(mc_result.gap_minutes),
                    gap_threshold_minutes=self.microcompact_config.gap_threshold_minutes,
                    tools_cleared=mc_result.tools_cleared,
                    tools_kept=mc_result.tools_kept,
                    tokens_saved=mc_result.tokens_saved,
                    keep_recent=self.microcompact_config.keep_recent,
                )
            )

        from src.engine_core.cached_snip import (
            cached_snip_prepass_enabled,
            is_model_supported_for_cache_editing,
            snip_prepass_get_deletes,
        )
        from src.engine_core.post_compact_cleanup import is_main_thread_compact

        adapter_model = getattr(self.adapter, "model", None)
        use_cached_path = (
            cached_snip_prepass_enabled()
            and hasattr(self.adapter, "queue_snip_cache_edits")
            and is_main_thread_compact(query_source)
            and is_model_supported_for_cache_editing(adapter_model)
        )
        if use_cached_path:
            tool_ids = snip_prepass_get_deletes(messages)
            if tool_ids:
                cast(Any, self.adapter).queue_snip_cache_edits(tool_ids)
        else:
            messages = snip_prepass(messages)

        if self.collapse_manager is not None and self.collapse_manager.enabled:
            collapse_pre_tok = estimate_messages_tokens(messages)
            collapse_pre_count = len(messages)
            try:
                self.collapse_manager.evaluate_and_stage(
                    messages,
                    collapse_pre_tok,
                    context_window,
                )
                messages = self.collapse_manager.project_messages(messages)
            except Exception:
                logger.exception("context_collapse: pre-pass failed, falling through")
            collapse_post_tok = estimate_messages_tokens(messages)
            collapse_post_count = len(messages)
            try:
                await self.collapse_manager.save_state(thread_id)
            except Exception:
                logger.debug("context_collapse: save_state failed (non-fatal)")
            if collapse_post_count < collapse_pre_count:
                await self.emitter.emit(
                    CompactionEvent(
                        thread_id=thread_id,
                        messages_before=collapse_pre_count,
                        messages_after=collapse_post_count,
                        tokens_before=collapse_pre_tok,
                        tokens_after=collapse_post_tok,
                        method=STRATEGY_CONTEXT_COLLAPSE,
                        chain_id=chain_id,
                        turn_id_before=turn_id,
                        turn_id_after=fire_chain_reset(),
                    )
                )
                self.history.append(
                    CompactionRecord(
                        timestamp=time.time(),
                        strategy=STRATEGY_CONTEXT_COLLAPSE,
                        tokens_before=collapse_pre_tok,
                        tokens_after=collapse_post_tok,
                        tokens_freed=collapse_pre_tok - collapse_post_tok,
                        messages_before=collapse_pre_count,
                        messages_after=collapse_post_count,
                        success=True,
                        turn_number=turn_number,
                    )
                )
            if not needs_compaction(collapse_post_tok, context_window):
                return CompactionResult(
                    messages=messages,
                    strategy=STRATEGY_CONTEXT_COLLAPSE,
                    tokens_freed=collapse_pre_tok - collapse_post_tok,
                    was_compacted=(collapse_post_count < collapse_pre_count),
                    microcompacted=microcompacted,
                )

        pre_tokens = estimate_messages_tokens(messages)
        pre_count = len(messages)

        pre_hook_veto = False
        pre_hook_contexts: list[str] = []
        threshold_compact_needed = needs_compaction(pre_tokens, context_window)
        if (
            self.lifecycle_bundle is not None
            and threshold_compact_needed
            and self.lifecycle_bundle.has_hooks(HookEvent.PRE_COMPACT)
        ):
            from src.engine_core.lifecycle_hooks import PreCompactInput

            pre_reg = self.lifecycle_bundle.registry(HookEvent.PRE_COMPACT)
            pre_inp = PreCompactInput(
                thread_id=thread_id,
                turn_number=turn_number,
                message_count=pre_count,
                token_estimate=pre_tokens,
                context_window=context_window,
                trigger_reason="threshold",
            )
            pre_agg = await pre_reg.execute_all(pre_inp)
            pre_hook_veto = pre_agg.prevent_continuation
            pre_hook_contexts = list(pre_agg.additional_contexts)
        if pre_hook_veto:
            return CompactionResult(
                messages=messages,
                strategy="skipped_by_hook",
                tokens_freed=0,
                was_compacted=False,
                hook_contexts=list(pre_hook_contexts),
                microcompacted=microcompacted,
            )

        strategy = "none"
        if threshold_compact_needed:
            if self.circuit_state == CircuitState.OPEN:
                logger.warning("Circuit breaker OPEN — using mechanical round-drop")
                from src.engine_core.context_engineering import drop_oldest_rounds

                cb_result = drop_oldest_rounds(messages)
                fallback: Literal["round_drop", ""] = ""
                if cb_result is not None:
                    messages = cb_result
                    strategy = "circuit_breaker_round_drop"
                    fallback = "round_drop"
                else:
                    strategy = "circuit_breaker_skip"
                await self.emitter.emit(
                    CompactionCircuitBreakerSkip(
                        thread_id=thread_id,
                        message_count=pre_count,
                        token_count=pre_tokens,
                        fallback_strategy=fallback,
                    )
                )
            else:
                if self.circuit_state == CircuitState.HALF_OPEN:
                    logger.info(
                        "Circuit breaker HALF_OPEN probe — attempting LLM compaction "
                        "(%d messages, %d tokens)",
                        pre_count,
                        pre_tokens,
                    )
                session_result = await self.try_session_memory_compact(
                    messages,
                    thread_id,
                    context_window,
                )
                if session_result is not None:
                    messages = session_result
                    strategy = "session_memory"
                    if self.circuit_state in (CircuitState.OPEN, CircuitState.HALF_OPEN):
                        await self.reset_breaker(
                            thread_id, "reset_success", "session_memory_success"
                        )
                else:
                    try:
                        logger.info(
                            "Starting LLM-based compaction (%d messages, %d tokens)",
                            pre_count,
                            pre_tokens,
                        )
                        messages = await compact_messages_with_summary(
                            messages,
                            self.adapter,
                            context_window,
                            system_prompt=system_prompt,
                            tools_schema=tools_schema,
                            on_ptl_event=self.emitter.emit,
                            ptl_thread_id=thread_id,
                        )
                        logger.info("LLM compaction completed (%d messages remain)", len(messages))
                        strategy = "summary"
                        if self.circuit_state in (CircuitState.OPEN, CircuitState.HALF_OPEN):
                            await self.reset_breaker(
                                thread_id, "reset_success", "llm_compact_success"
                            )
                        else:
                            self.consecutive_failures = 0
                    except Exception as exc:
                        logger.exception("LLM compaction failed, falling back to mechanical")
                        was_probe = self.circuit_state == CircuitState.HALF_OPEN
                        if was_probe:
                            self.circuit_state = CircuitState.OPEN
                        self.consecutive_failures += 1
                        if self.consecutive_failures >= self.config.circuit_breaker_max_failures:
                            reason = (
                                "probe_failed_cooldown_reset"
                                if was_probe
                                else f"max_failures_{self.config.circuit_breaker_max_failures}"
                            )
                            await self.trip_breaker(
                                thread_id,
                                error=str(exc),
                                message_count=pre_count,
                                token_count=pre_tokens,
                                reason=reason,
                            )
                        messages = compact_messages(messages, context_window)
                        strategy = "mechanical"
        else:
            messages = compact_messages(messages, context_window)
            if len(messages) < pre_count:
                strategy = "mechanical"

        if strategy == "mechanical":
            await self.check_context_shrink_reset(
                thread_id,
                pre_tokens=pre_tokens,
                post_tokens=estimate_messages_tokens(messages),
            )

        messages = guard_tool_results(
            messages, context_window, replacement_state=self.replacement_state
        )

        post_tokens = estimate_messages_tokens(messages)
        post_count = len(messages)

        if post_count < pre_count:
            await self.emitter.emit(
                CompactionEvent(
                    thread_id=thread_id,
                    messages_before=pre_count,
                    messages_after=post_count,
                    tokens_before=pre_tokens,
                    tokens_after=post_tokens,
                    method=strategy,
                    chain_id=chain_id,
                    turn_id_before=turn_id,
                    turn_id_after=fire_chain_reset(),
                )
            )

        sys_tokens = estimate_system_tokens(system_prompt)
        tools_tokens = (
            sum(estimate_tokens(json.dumps(t, default=str)) for t in tools_schema)
            if tools_schema
            else 0
        )
        preflight_total = post_tokens + sys_tokens + tools_tokens + RESERVE_FLOOR

        if preflight_total >= context_window:
            logger.warning(
                "Pre-flight budget exceeded: %d / %d — emergency compact",
                preflight_total,
                context_window,
            )
            em_before = len(messages)
            em_tok_before = post_tokens
            messages = emergency_compact(messages, context_window)
            post_tokens = estimate_messages_tokens(messages)
            strategy = "emergency"
            await self.emitter.emit(
                CompactionEvent(
                    thread_id=thread_id,
                    messages_before=em_before,
                    messages_after=len(messages),
                    tokens_before=em_tok_before,
                    tokens_after=post_tokens,
                    method="emergency",
                    chain_id=chain_id,
                    turn_id_before=turn_id,
                    turn_id_after=fire_chain_reset(),
                )
            )

        tokens_freed = pre_tokens - post_tokens
        was_compacted = strategy != "none"
        if was_compacted:
            messages = await self.finalize_compaction(
                messages,
                thread_id=thread_id,
                turn_number=turn_number,
                strategy=strategy,
                tokens_before=pre_tokens,
                tokens_after=post_tokens,
                pre_count=pre_count,
                query_source=query_source,
            )

        post_hook_contexts: list[str] = []
        if was_compacted:
            post_hook_contexts = await self.fire_post_compact_hook(
                thread_id=thread_id,
                turn_number=turn_number,
                strategy=strategy,
                pre_count=pre_count,
                post_count=len(messages),
                pre_tokens=pre_tokens,
                post_tokens=post_tokens,
            )

        return CompactionResult(
            messages=messages,
            strategy=strategy,
            tokens_freed=tokens_freed,
            was_compacted=was_compacted,
            hook_contexts=pre_hook_contexts + post_hook_contexts,
            microcompacted=microcompacted,
        )

    async def reactive_compact(
        self,
        messages: list,
        overflow_attempt: int,
        context_window: int,
        *,
        thread_id: str = "",
        turn_number: int = 0,
        query_source: str | None = None,
        chain_id: str = "",
        turn_id: str = "",
    ) -> "ReactiveCompactResult":
        """Called when the API returns a context overflow error.

        Graduated recovery (cheapest intervention first):
         -1: Context-collapse drain: commit staged spans and reproject
             (no LLM call). Skipped when the manager is absent, disabled,
             or has nothing staged, or when the drained list still needs
             compaction.
          0: Forced microcompact + normal compaction with tighter preserve (10).
          1: Aggressive tool result compaction (whole-block guard).
          2+: Emergency — keep first + last 5.

        Returns ReactiveCompactResult with made_progress=False when the tier
        could not reduce token count. The caller uses this to skip retrying the
        API with identical messages and escalate to the next tier instead.

        Caller contract: overflow_attempt is the tier the caller wants tried.
        thread.py drives it from two nested loops: the per-turn
        `for overflow_attempt in range(MAX_OVERFLOW_RETRIES + 1)` retry loop,
        and, on a single 413, an inner
        `for tier in range(overflow_attempt, MAX_OVERFLOW_RETRIES + 1)` sweep.
        That inner sweep is what bounds recovery. A tier that returns
        made_progress=False escalates to the next tier immediately and
        in-process, costing a scan rather than another API request, and running
        out of tiers raises CONTEXT_OVERFLOW_UNRECOVERABLE instead of retrying
        forever.

        The stop-hook sweeps live in the turn loop, outside the overflow loop,
        so the turn a blocking stop hook forces starts the ladder again at tier
        0 rather than resuming where the previous turn left off. That is the
        intended behavior: the retry turn carries a system-reminder message the
        previous attempt did not, so the cheap tiers are worth re-trying, and
        seeding it at a high tier would reach for the destructive strategies
        (emergency keeps first + last 5) before the cheap ones have been given
        the new list. Restarting cannot spiral, because two bounds hold it: the
        no-progress escalation above never re-hits the API on a tier that freed
        nothing, and the caller's stop_hook_active guard lets a blocking hook
        force exactly one retry, after which the end-of-turn branch falls
        through to the loop break until a clean post-tool-use sweep clears the
        flag.
        """
        from src.engine_core.context_engineering import (
            compact_messages,
            guard_tool_results,
            emergency_compact,
            repair_tool_use_result_pairing,
            estimate_messages_tokens,
            needs_compaction,
        )
        from src.engine_core.events import MicrocompactEvent, ReactiveCompactionEvent
        from src.engine_core.time_based_microcompact import (
            maybe_microcompact,
            TriggerMode,
        )

        pre_tokens = estimate_messages_tokens(messages)
        pre_count = len(messages)

        if (
            self.collapse_manager is not None
            and self.collapse_manager.enabled
            and self.collapse_manager.has_staged()
        ):
            try:
                self.collapse_manager.commit_all_staged()
                messages = self.collapse_manager.project_messages(messages)
                messages = repair_tool_use_result_pairing(messages)
            except Exception:
                logger.exception("context_collapse: 413 rescue failed, falling through")
            drain_tokens = estimate_messages_tokens(messages)
            if not needs_compaction(drain_tokens, context_window):
                logger.info(
                    "reactive_compact tier=-1 strategy=collapse_drain "
                    "pre_tokens=%d post_tokens=%d freed=%d",
                    pre_tokens,
                    drain_tokens,
                    pre_tokens - drain_tokens,
                )
                messages = await self.finalize_compaction(
                    messages,
                    thread_id=thread_id,
                    turn_number=turn_number,
                    strategy="reactive_collapse_drain",
                    tokens_before=pre_tokens,
                    tokens_after=drain_tokens,
                    pre_count=pre_count,
                    query_source=query_source,
                )
                await self.emitter.emit(
                    ReactiveCompactionEvent(
                        thread_id=thread_id,
                        tier=-1,
                        strategy="collapse_drain",
                        tokens_before=pre_tokens,
                        tokens_after=drain_tokens,
                        messages_before=pre_count,
                        messages_after=len(messages),
                        overflow_attempt=overflow_attempt,
                        made_progress=True,
                    )
                )
                await self.fire_post_compact_hook(
                    thread_id=thread_id,
                    turn_number=turn_number,
                    strategy="reactive_collapse_drain",
                    pre_count=pre_count,
                    post_count=len(messages),
                    pre_tokens=pre_tokens,
                    post_tokens=drain_tokens,
                )
                return ReactiveCompactResult(
                    messages=messages,
                    strategy="collapse_drain",
                    tokens_before=pre_tokens,
                    tokens_after=drain_tokens,
                    made_progress=True,
                    tier=-1,
                )

        strategy = "none"
        tier = overflow_attempt

        if overflow_attempt == 0:
            mc_result = maybe_microcompact(
                messages,
                self.microcompact_config,
                TriggerMode.FORCED,
            )
            if mc_result is not None:
                messages = mc_result.messages
                await self.emitter.emit(
                    MicrocompactEvent(
                        thread_id=thread_id,
                        mode=mc_result.mode.value,
                        gap_minutes=round(mc_result.gap_minutes),
                        gap_threshold_minutes=self.microcompact_config.gap_threshold_minutes,
                        tools_cleared=mc_result.tools_cleared,
                        tools_kept=mc_result.tools_kept,
                        tokens_saved=mc_result.tokens_saved,
                        keep_recent=self.microcompact_config.keep_recent,
                    )
                )
            messages = compact_messages(messages, context_window, preserve_recent=10)
            messages = repair_tool_use_result_pairing(messages)
            strategy = "microcompact+mechanical"
        elif overflow_attempt == 1:
            messages = guard_tool_results(
                messages, context_window, replacement_state=self.replacement_state
            )
            messages = repair_tool_use_result_pairing(messages)
            strategy = "guard"
        else:
            messages = emergency_compact(messages, context_window, preserve_recent=5)
            strategy = "emergency"

        post_tokens = estimate_messages_tokens(messages)
        post_count = len(messages)
        made_progress = post_tokens < pre_tokens

        logger.info(
            "reactive_compact tier=%d strategy=%s pre_count=%d post_count=%d "
            "pre_tokens=%d post_tokens=%d freed=%d made_progress=%s",
            tier,
            strategy,
            pre_count,
            post_count,
            pre_tokens,
            post_tokens,
            pre_tokens - post_tokens,
            made_progress,
        )

        if made_progress:
            messages = await self.finalize_compaction(
                messages,
                thread_id=thread_id,
                turn_number=turn_number,
                strategy=f"reactive_{strategy}",
                tokens_before=pre_tokens,
                tokens_after=post_tokens,
                pre_count=pre_count,
                query_source=query_source,
            )

        await self.emitter.emit(
            ReactiveCompactionEvent(
                thread_id=thread_id,
                tier=tier,
                strategy=strategy,
                tokens_before=pre_tokens,
                tokens_after=post_tokens,
                messages_before=pre_count,
                messages_after=len(messages),
                overflow_attempt=overflow_attempt,
                made_progress=made_progress,
            )
        )

        if made_progress:
            await self.fire_post_compact_hook(
                thread_id=thread_id,
                turn_number=turn_number,
                strategy=f"reactive_{strategy}",
                pre_count=pre_count,
                post_count=post_count,
                pre_tokens=pre_tokens,
                post_tokens=post_tokens,
            )

        return ReactiveCompactResult(
            messages=messages,
            strategy=strategy,
            tokens_before=pre_tokens,
            tokens_after=post_tokens,
            made_progress=made_progress,
            tier=tier,
        )

    async def fire_post_compact_hook(
        self,
        *,
        thread_id: str,
        turn_number: int,
        strategy: str,
        pre_count: int,
        post_count: int,
        pre_tokens: int,
        post_tokens: int,
    ) -> list[str]:
        """Dispatch PostCompact lifecycle hooks and return collected
        ``additional_contexts``. Shared between proactive
        ``compact_if_needed`` and reactive ``reactive_compact`` so both
        paths honor the "fires after any compaction" contract.
        Returns ``[]`` when no bundle or no hooks are registered (zero
        overhead — has_hooks is a .get() check that does not create the
        registry).
        """
        if self.lifecycle_bundle is None or not self.lifecycle_bundle.has_hooks(
            HookEvent.POST_COMPACT
        ):
            return []
        reg = self.lifecycle_bundle.registry(HookEvent.POST_COMPACT)
        from src.engine_core.lifecycle_hooks import PostCompactInput

        sha = ""
        try:
            sha = hashlib.sha256(COMPACTION_BOUNDARY_CONTENT.encode("utf-8")).hexdigest()[:16]
        except Exception:
            sha = ""
        inp = PostCompactInput(
            thread_id=thread_id,
            turn_number=turn_number,
            before_message_count=pre_count,
            after_message_count=post_count,
            before_token_estimate=pre_tokens,
            after_token_estimate=post_tokens,
            strategy=strategy,
            summary_sha=sha,
        )
        try:
            agg = await reg.execute_all(inp)
        except Exception as exc:
            logger.debug("PostCompact hook dispatch failed: %s", exc)
            return []
        return list(agg.additional_contexts)

    def calculate_messages_to_keep_index(self, messages: list) -> int:
        """Token-aware boundary: walk backward from end keeping messages until
        min_tokens AND min_text_block_messages are satisfied, capped at max_tokens."""
        from src.engine_core.context_engineering import estimate_messages_tokens
        from src.herald.types import TextBlock

        cfg = self.config
        accumulated_tokens = 0
        text_block_count = 0
        idx = len(messages)

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            msg_tokens = estimate_messages_tokens([msg])

            if accumulated_tokens + msg_tokens > cfg.max_tokens:
                break

            accumulated_tokens += msg_tokens
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                text_block_count += 1
            elif isinstance(content, list):
                if any(isinstance(b, TextBlock) for b in content):
                    text_block_count += 1

            idx = i

            if (
                accumulated_tokens >= cfg.min_tokens
                and text_block_count >= cfg.min_text_block_messages
            ):
                break

        return idx

    @staticmethod
    def adjust_keep_index(messages: list, start_index: int) -> int:
        """Backward scan to prevent orphaned tool_use/tool_result pairs.

        When slicing messages at start_index, some ToolResultBlocks in the
        kept range may reference ToolUseBlocks that are *before* the slice
        point. This scans backward to include those assistant messages,
        preventing API 400 errors from mismatched pairs.
        """
        from src.herald.types import ToolUseBlock, ToolResultBlock

        result_ids: set[str] = set()
        use_ids_in_range: set[str] = set()
        for msg in messages[start_index:]:
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        result_ids.add(block.tool_use_id)
                    elif isinstance(block, ToolUseBlock):
                        use_ids_in_range.add(block.id)

        needed = result_ids - use_ids_in_range
        if not needed:
            return start_index

        adjusted = start_index
        for i in range(start_index - 1, -1, -1):
            if not needed:
                break
            content = getattr(messages[i], "content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolUseBlock) and block.id in needed:
                        adjusted = i
                        needed.discard(block.id)

        return adjusted

    async def try_session_memory_compact(
        self,
        messages: list,
        thread_id: str,
        context_window: int,
    ) -> list | None:
        """Try to compact using Working Memory entries instead of an LLM call.

        Returns None if WM has no useful content (caller falls through to LLM).
        Uses adjust_keep_index to prevent orphaned tool_use/tool_result pairs.
        """
        from src.herald.types import Message as LLMMessage
        from src.engine_core.context_engineering import (
            estimate_messages_tokens,
            needs_compaction,
            repair_tool_use_result_pairing,
        )

        try:
            summary = await self.memory.get_context_summary(
                max_items=50,
                thread_id=thread_id,
                max_tokens=self.config.max_tokens,
            )
        except Exception:
            logger.debug("WM get_context_summary failed, skipping session memory compact")
            return None

        if not summary or len(summary.strip()) < 50:
            return None

        raw_start = self.calculate_messages_to_keep_index(messages)
        adjusted_start = self.adjust_keep_index(messages, raw_start)
        recent = [
            m
            for m in messages[adjusted_start:]
            if not (
                isinstance(getattr(m, "content", ""), str)
                and COMPACTION_BOUNDARY_CONTENT in m.content
            )
        ]

        summary_msg = LLMMessage(
            role="user",
            content=(
                "This session is being continued from a previous conversation "
                "that ran out of context.\n"
                "The summary below covers the earlier portion of the conversation.\n\n"
                f"{summary}\n\n"
                "Recent messages are preserved verbatim."
            ),
            timestamp=time.time(),
        )
        result = [summary_msg] + recent
        result = repair_tool_use_result_pairing(result)

        if needs_compaction(estimate_messages_tokens(result), context_window):
            return None

        if self.last_summarized_msg_index is None:
            logger.info(
                "First session memory compaction (or resumed session): %d -> %d messages (no LLM call)",
                len(messages),
                len(result),
            )
        else:
            logger.info(
                "Session memory compaction: %d -> %d messages (no LLM call)",
                len(messages),
                len(result),
            )
        self.last_summarized_msg_index = adjusted_start
        self.consecutive_failures = 0
        return result

    async def finalize_compaction(
        self,
        messages: list,
        *,
        thread_id: str,
        turn_number: int,
        strategy: str,
        tokens_before: int,
        tokens_after: int,
        pre_count: int,
        query_source: str | None = None,
    ) -> list:
        """Shared post-compaction finalization for both proactive and reactive paths.

        Appends boundary marker, re-injects mission/charter context, restores
        recently-read files, records history, and runs cache cleanup.
        """
        from src.herald.types import Message as LLMMessage
        from src.engine_core.context_engineering import PRESERVE_RECENT

        self.history.append(
            CompactionRecord(
                timestamp=time.time(),
                strategy=strategy,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                tokens_freed=tokens_before - tokens_after,
                messages_before=pre_count,
                messages_after=len(messages),
                success=True,
                turn_number=turn_number,
            )
        )
        discovered_tools = extract_discovered_tool_names(messages)
        tools_attr = ""
        if discovered_tools:
            tools_attr = f" discovered_tools={','.join(sorted(discovered_tools))}"
        boundary_msg = LLMMessage(
            role="user",
            content=(
                f"{COMPACTION_BOUNDARY_CONTENT}\n"
                f"<!-- compact_meta: tokens_before={tokens_before} "
                f"tokens_after={tokens_after} strategy={strategy} "
                f"ts={time.time():.0f}{tools_attr} -->"
            ),
            timestamp=time.time(),
        )
        messages = messages + [boundary_msg]
        reinjection = await self.build_reinjection_messages(thread_id)
        if reinjection:
            messages = messages + reinjection
        file_msgs = await self.build_file_restoration_messages(
            messages[-PRESERVE_RECENT:] if len(messages) > PRESERVE_RECENT else messages,
            thread_id=thread_id,
        )
        if file_msgs:
            messages = messages + file_msgs
        tool_reminder = self.build_tool_reannouncement_messages(discovered_tools)
        if tool_reminder:
            messages = messages + tool_reminder
        skill_msgs = self.build_skill_reinjection_messages()
        if skill_msgs:
            messages = messages + skill_msgs
        self.post_compact_cleanup(
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            strategy=strategy,
        )
        self.last_summarized_msg_index = None
        return messages

    def post_compact_cleanup(
        self,
        *,
        tokens_before: int = 0,
        tokens_after: int = 0,
        strategy: str = "",
    ) -> None:
        """Invalidate caches and clean up state after compaction.

        Delegates the cache-reset matrix to run_post_compact_cleanup so the
        "what gets reset" list has a single source of truth. Keeps the
        tokens-before/after observability log on the orchestrator because
        it belongs to the compaction event, not to the generic cleanup.
        """
        from src.engine_core.post_compact_cleanup import run_post_compact_cleanup

        run_post_compact_cleanup(
            consciousness_mgr=self.consciousness_mgr,
            file_read_state=self.file_read_state,
            search=self.search,
            utility=self.utility,
            assets=self.assets,
            collapse_manager=self.collapse_manager,
            llm_provider=self.adapter,
        )
        if tokens_before:
            logger.info(
                "Compaction complete (%s): %d -> %d tokens (freed %d)",
                strategy,
                tokens_before,
                tokens_after,
                tokens_before - tokens_after,
            )

    async def build_file_restoration_messages(
        self,
        preserved_messages: list,
        *,
        thread_id: str = "",
    ) -> list:
        """Re-read the most recently accessed files after compaction.

        Sorts tracked files by recency, takes top POST_COMPACT_MAX_FILES,
        skips files whose path appears in preserved message content,
        reads from disk in parallel within per-file and total token budgets.

        Emits per-file FileRestorationEvent/FileRestorationFailedEvent for
        observability. Uses proper token estimation for budget math and
        accounts for the truncation marker in the char budget.
        """
        import asyncio
        from src.herald.types import Message as LLMMessage
        from src.engine_core.context_engineering import estimate_tokens_dense
        from src.engine_core.events import FileRestorationEvent, FileRestorationFailedEvent

        if not self.file_read_state:
            return []

        preserved_paths = collect_file_paths_from_messages(preserved_messages)

        sorted_files = sorted(
            self.file_read_state.items(),
            key=lambda x: x[1].timestamp,
            reverse=True,
        )

        candidates: list[str] = []
        for path, _ in sorted_files:
            if len(candidates) >= POST_COMPACT_MAX_FILES:
                break
            if path in preserved_paths:
                continue
            if should_exclude_from_restore(path):
                continue
            candidates.append(path)

        if not candidates:
            return []

        async def read_file(path: str) -> tuple[str, str | None]:
            try:
                content = await asyncio.to_thread(read_file_sync, path)
                return path, content
            except (OSError, IOError):
                return path, None

        results = await asyncio.gather(*[read_file(p) for p in candidates])

        marker_tokens = estimate_tokens_dense(TRUNCATION_MARKER)
        per_file_char_budget = tokens_to_dense_chars(
            POST_COMPACT_MAX_TOKENS_PER_FILE,
            marker_tokens=marker_tokens,
        )

        messages: list = []
        total_tokens = 0
        for path, content in results:
            if content is None:
                await self.emitter.emit(
                    FileRestorationFailedEvent(
                        path=path,
                        reason="read_error",
                        thread_id=thread_id,
                    )
                )
                continue
            if total_tokens >= POST_COMPACT_TOKEN_BUDGET:
                break

            truncated = False

            toctou_note = ""
            record = self.file_read_state.get(path)
            if record and record.content_hash:
                current_hash = hashlib.sha256(content.encode(errors="replace")).hexdigest()
                if current_hash != record.content_hash:
                    toctou_note = (
                        f"[Note: file modified since last read at "
                        f"{time.strftime('%H:%M:%S', time.localtime(record.timestamp))}]\n"
                    )

            if len(content) > per_file_char_budget:
                content = content[:per_file_char_budget] + TRUNCATION_MARKER
                truncated = True

            file_tokens = estimate_tokens_dense(content)
            if total_tokens + file_tokens > POST_COMPACT_TOKEN_BUDGET:
                remaining_tokens = POST_COMPACT_TOKEN_BUDGET - total_tokens
                remaining_chars = tokens_to_dense_chars(
                    remaining_tokens, marker_tokens=marker_tokens
                )
                content = content[: max(0, remaining_chars)] + TRUNCATION_MARKER
                file_tokens = estimate_tokens_dense(content)
                truncated = True

            total_tokens += file_tokens
            messages.append(
                LLMMessage(
                    role="user",
                    content=f"[Post-compact file context] {path}:\n{toctou_note}{content}",
                    timestamp=time.time(),
                )
            )

            await self.emitter.emit(
                FileRestorationEvent(
                    path=path,
                    tokens=file_tokens,
                    truncated=truncated,
                    thread_id=thread_id,
                )
            )

        if messages:
            logger.info(
                "Post-compact file restoration: %d files, ~%d tokens",
                len(messages),
                total_tokens,
            )
        return messages

    def build_tool_reannouncement_messages(self, discovered_tools: set[str]) -> list:
        """Build a reminder message listing tools that survive compaction."""
        if not discovered_tools or not post_compact_hardened_enabled():
            return []
        from src.herald.types import Message as LLMMessage
        from src.engine_core.context_engineering import estimate_tokens_dense

        sorted_tools = sorted(discovered_tools)
        content = (
            "[Post-compact tool reminder] These tools remain available "
            "and can be called: " + ", ".join(sorted_tools)
        )
        tokens = estimate_tokens_dense(content)
        if tokens > POST_COMPACT_TOOL_REMINDER_TOKEN_BUDGET:
            parts = ["[Post-compact tool reminder] These tools remain available and can be called:"]
            budget_chars = tokens_to_dense_chars(POST_COMPACT_TOOL_REMINDER_TOKEN_BUDGET)
            running = len(parts[0])
            for t in sorted_tools:
                if running + len(t) + 2 > budget_chars:
                    parts.append(f"... and {len(sorted_tools) - len(parts) + 1} more")
                    break
                parts.append(t)
                running += len(t) + 2
            content = " ".join(parts)
        return [LLMMessage(role="user", content=content, timestamp=time.time())]

    def build_skill_reinjection_messages(self) -> list:
        """Re-inject matched skill context after compaction if available."""
        if not self.last_matched_skills or not post_compact_hardened_enabled():
            return []
        if not self.utility:
            return []
        from src.herald.types import Message as LLMMessage
        from src.engine_core.context_engineering import estimate_tokens_dense

        try:
            parts = ["[Post-compact skill context] Relevant skills for this task:"]
            total_tokens = 0
            for skill in self.last_matched_skills:
                result = self.utility.skill_execute(skill)
                prompt = result.get("prompt", "")
                if not prompt:
                    continue
                skill_tokens = estimate_tokens_dense(prompt)
                if skill_tokens > POST_COMPACT_MAX_TOKENS_PER_SKILL:
                    char_limit = tokens_to_dense_chars(POST_COMPACT_MAX_TOKENS_PER_SKILL)
                    prompt = prompt[:char_limit] + "\n[... skill truncated]"
                    skill_tokens = estimate_tokens_dense(prompt)
                if total_tokens + skill_tokens > POST_COMPACT_SKILL_TOKEN_BUDGET:
                    break
                total_tokens += skill_tokens
                parts.append(prompt)
            if len(parts) <= 1:
                return []
            return [LLMMessage(role="user", content="\n".join(parts), timestamp=time.time())]
        except Exception:
            logger.debug("Skill reinjection failed (non-fatal)", exc_info=True)
            return []

    async def build_reinjection_messages(self, thread_id: str) -> list:
        """Re-inject critical context after compaction.

        Injects charter/mission context and working memory checkpoints
        so the model doesn't lose information across the compaction boundary.
        """
        from src.herald.types import Message as LLMMessage

        parts: list[str] = []

        if self.charter is not None:
            try:
                if hasattr(self.charter, "goal"):
                    parts.append(f"Active mission: {self.charter.goal}")
                    if hasattr(self.charter, "nodes") and self.charter.nodes:
                        node_labels = [getattr(n, "label", str(n)) for n in self.charter.nodes[:5]]
                        parts.append(f"Charter nodes: {', '.join(node_labels)}")
            except Exception:
                logger.debug("Re-injection: charter extraction failed")

        try:
            from src.memory.working_memory import EntryType

            checkpoints = await self.memory.read_by_type(thread_id, EntryType.CHECKPOINT)
            for cp in checkpoints[-3:]:
                parts.append(f"Checkpoint: {cp.content[:500]}")
        except Exception:
            logger.debug("Re-injection: WM checkpoint read failed")

        try:
            from src.memory.working_memory import EntryType

            events = await self.memory.read_by_type(thread_id, EntryType.SYSTEM_EVENT)
            for ev in events[-5:]:
                content = ev.content[:300]
                if "_struggle" not in content:
                    parts.append(f"Event: {content}")
        except Exception:
            logger.debug("Re-injection: WM event read failed")

        if not parts:
            return []

        context_block = "\n".join(parts)
        return [
            LLMMessage(
                role="user",
                content=f"[Context restored after compaction]\n{context_block}",
                timestamp=time.time(),
            )
        ]

    async def reset_circuit_breaker(self, thread_id: str, transition: str, reason: str) -> None:
        """Backward-compat shim — delegates to reset_breaker()."""
        await self.reset_breaker(thread_id, transition, reason)

    def reset(self) -> None:
        """Reset state for a new session.

        Clears ALL metrics including session-level trip/probe counters.
        Per spec: total_trips, total_probes, total_probe_successes are
        cleared on full session reset but persist across
        reset_for_new_user_message() calls within a session.
        """
        self.consecutive_failures = 0
        self.circuit_state = CircuitState.CLOSED
        self.tripped_at = None
        self.last_message_count_at_trip = 0
        self.last_token_count_at_trip = 0
        self.last_trip_error = ""
        self.total_trips = 0
        self.total_probes = 0
        self.total_probe_successes = 0
        self.history.clear()
        self.file_read_state.clear()
        self.last_matched_skills.clear()
        self.last_summarized_msg_index = None
