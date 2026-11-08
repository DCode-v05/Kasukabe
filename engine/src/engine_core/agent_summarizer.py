"""Background summarizer — periodic LLM fork that produces a short
progress summary of a running SubAgent.

Mirrors startAgentSummarization() in the reference. Drives on a simple
asyncio.Timer: every `interval_s` seconds, fork a fire-and-forget LLM call
with the accumulated messages, ask for a one-sentence summary, emit a
SubAgentSummary event. Summarizer calls set skip_cache_write=True and send
tools=[] so the throwaway summary never pollutes the parent's prompt cache —
this trades the cache hit for isolation. Intentional: the summarizer is an
observability side-channel, not a cost-sensitive hot path.

Safe to leave disabled — summarizer is opt-in via enable=True on __init__
and/or the AGENT_SUMMARIZATION feature flag (env var "true"/"false").
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

from src.engine_core.events import EventEmitter, SubAgentSummary
from src.herald import LLM
from src.herald.types import Message as LLMMessage, SystemPromptBlock


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 15.0

# Literal directive appended to the summarization prompt so the model knows
# to stop chasing the original task and just produce a terse status line.
SUMMARIZE_DIRECTIVE = (
    "Summarize the current progress of this background worker in ONE sentence. "
    "No preamble, no suggestions, no lists. Just the sentence. "
    "Under 30 words."
)


def is_summarization_enabled() -> bool:
    """Respect AGENT_SUMMARIZATION env var; default disabled to avoid
    surprise spend. String values only ("true"/"false" per project
    engineering standards — never "1"/"0")."""
    return os.environ.get("AGENT_SUMMARIZATION", "false").strip().lower() == "true"


@dataclass
class SummarizerConfig:
    interval_s: float = DEFAULT_INTERVAL_S
    enable: bool = False
    # Hard ceiling on how many summarization calls can fire for one agent.
    # Prevents a runaway watchdog from draining the budget if the agent loop
    # is stuck and never calls cancel().
    max_calls: int = 10


class AgentSummarizer:
    """Periodic progress summarizer bound to a single SubAgent.

    Lifecycle: call start() once at the top of the run loop; call
    record_messages() each turn to hand it the latest conversation state;
    call stop() from the finally block.
    """

    def __init__(
        self,
        *,
        adapter: LLM,
        emitter: EventEmitter,
        thread_id: str,
        agent_id: str,
        agent_name: str,
        system_prompt: list[SystemPromptBlock] | None,
        chain_fields: dict | None = None,
        config: SummarizerConfig | None = None,
    ) -> None:
        self.adapter = adapter
        self.emitter = emitter
        self.thread_id = thread_id
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.system_prompt = system_prompt
        self.chain_fields = chain_fields or {}
        self.config = config or SummarizerConfig()
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()
        self.messages: list[LLMMessage] = []
        self.turn: int = 0
        self.calls_fired = 0
        # Last turn we actually fired for — blocks firing twice on the same
        # conversation snapshot (e.g. agent stalled in a long tool call).
        self.last_fired_turn: int = -1

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        if not self.config.enable:
            return
        if self.task is not None:
            return  # Already running.
        self.task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
            except Exception:
                logger.debug("summarizer stop raised", exc_info=True)
            self.task = None

    def record_messages(self, messages: list[LLMMessage], turn: int) -> None:
        """Snapshot the current conversation for the next summarization run.

        The summarizer only reads; the run loop keeps ownership of the list.
        """
        self.messages = list(messages)
        self.turn = turn

    # ── internals ─────────────────────────────────────────────────

    async def loop(self) -> None:
        """Drive the summarization cadence until stop() or max_calls hit."""
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.config.interval_s,
                )
                return  # stop() fired.
            except asyncio.TimeoutError:
                pass
            if self.calls_fired >= self.config.max_calls:
                logger.debug(
                    "summarizer %s hit max_calls=%d",
                    self.agent_id,
                    self.config.max_calls,
                )
                return
            if not self.messages:
                continue
            if self.turn == self.last_fired_turn:
                # No turn advance since last fire — skip duplicate summary.
                continue
            self.last_fired_turn = self.turn
            await self.fire()

    async def fire(self) -> None:
        """Run one summarization call and emit the event.

        Swallows every error — summarization is observability, never critical
        path. The fork uses skip_cache_write=True so the throwaway response
        doesn't poison the main agent's prompt cache.
        """
        self.calls_fired += 1
        snapshot = list(self.messages)
        snapshot.append(
            LLMMessage(
                role="user",
                content=SUMMARIZE_DIRECTIVE,
                timestamp=time.time(),
            )
        )
        try:
            response = await self.adapter.generate_with_tools(
                snapshot,
                system=self.system_prompt,
                tools=[],
                skip_cache_write=True,
            )
        except Exception:
            logger.debug("summarizer LLM call failed", exc_info=True)
            return

        text = (response.text or "").strip()
        if not text:
            return

        try:
            await self.emitter.emit(
                SubAgentSummary(
                    thread_id=self.thread_id,
                    agent_id=self.agent_id,
                    agent_name=self.agent_name,
                    summary=text,
                    turn=self.turn,
                    chain_id=self.chain_fields.get("chain_id", ""),
                    depth=self.chain_fields.get("depth", 0),
                )
            )
        except Exception:
            logger.debug("summarizer emit failed", exc_info=True)


__all__ = [
    "AgentSummarizer",
    "SummarizerConfig",
    "DEFAULT_INTERVAL_S",
    "is_summarization_enabled",
]
