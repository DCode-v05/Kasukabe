"""Memory stream: the agent's own turns, written as retrievable memories.

The `memories` table (supabase/migrations/20260730121100_memories.sql) has
existed since genesis, is queried by `world/agent_viewer_api.py`, and was
never written to by anything - the same shape of gap RC-2 found in
`utterances` (ROADMAP.md section 1.2: "the utterances table wrote zero
rows because there was [no speech tool]"). An agent's memory panel was
therefore permanently empty, and nothing the town could see connected an
engine's actual reasoning to the record the research is supposed to read.

This module is the producer half of that pipeline. It is entirely inside
`engine/src/sim/*` - the changeable seam (ROADMAP.md's Law 1) - and
touches nothing under `src/engine_core/`, `src/memory/`, `src/herald/` or
`src/learning_centre/`. Like session_log.py it works by subscribing to
`EventEmitter`, the frozen engine's own documented pub/sub extension
point, so the engine core is unchanged.

What it writes, and why those three types:

  - **observation** - one per completed tool call. This is what the agent
    actually did and what came back, which is the ground truth every
    later reflection is synthesized from (Generative Agents, arXiv
    2304.03442; DESIGN.md's intro cites it as the mechanism this project
    is inspired by).
  - **plan** - the agent's stated intent for the turn, taken from its
    reasoning. A plan is what distinguishes an agent from a reflex.
  - **reflection** - written when a turn's reasoning refers back to
    earlier turns rather than only to the perception in front of it, and
    carries `related_memory_ids` pointing at the observations it was
    drawn from. Those citations are what make the stream a chain rather
    than a flat log, which is the property the paper's retrieval depends
    on.

Importance (1-10) drives retrieval salience in that same design, so it is
scored here rather than left constant: a memory that cost money, changed
the world, or failed matters more than one that did not. The scoring is a
heuristic and says so - see `score_importance`.

Dream probes are deliberately never written (DESIGN.md section 10: "never
written to agent memory"); nothing in this module can produce one, and the
table's own CHECK constraint makes it impossible anyway.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.sandbox.permissions import PermissionBehavior, PermissionRule, ToolSource
from src.utility_directory.tool_runtime import ToolDef

from src.engine_core.events import (
    Event,
    EventEmitter,
    ExecutionSummary,
    TextDelta,
    ThinkingDelta,
    ToolExecution,
)

MEMORY_STREAM_FILENAME = "memory_stream.jsonl"

CONSEQUENTIAL_TOOLS = frozenset({
    "buy", "sell", "transfer", "transfer_tile", "auction_land", "set_will",
    "marry", "come_of_age", "build_structure", "register_business",
    "hire_employee", "fire_employee", "vote", "petition", "steal",
    "file_complaint", "teach_skill", "say", "pray",
})

UNREMARKABLE_WHEN_THEY_SUCCEED = frozenset({
    "rest", "look_around", "list_places", "describe_place", "describe_person",
    "describe_primitives", "check_debts", "recall_goals", "recall_memories",
    "recall_session_log", "quote_action_cost", "find_people", "search_memory",
})

_BACKWARD_LOOKING = re.compile(
    r"\b(earlier|before|last time|previously|already|again|yesterday|"
    r"i (?:have|had|did|tried|learned|noticed)|so far)\b",
    re.IGNORECASE,
)
_INTENT = re.compile(
    r"\b(i (?:will|should|am going to|need to|plan to|want to)|next,? i|"
    r"my plan|the plan is|i'?ll)\b",
    re.IGNORECASE,
)

MAX_CONTENT_CHARS = 600

_THINK_CLOSE = "</think>"
_THINK_OPEN = "<think>"


def reasoning_from_text(text: str) -> str:
    """The deliberating part of a turn's TEXT output.

    When the model marks its reasoning with <think></think>, everything up
    to the last closing tag is the reasoning and the rest is what it said
    out loud. When it does not mark anything, the text is taken as it
    stands: an unmarked monologue before the first tool call is still the
    turn's reasoning, and a turn that only talks has nothing else to offer.
    """
    close = text.rfind(_THINK_CLOSE)
    if close != -1:
        text = text[:close]
        open_at = text.find(_THINK_OPEN)
        if open_at != -1:
            text = text[open_at + len(_THINK_OPEN) :]
    return text.strip()


def score_importance(memory_type: str, *, tool: str | None = None, failed: bool = False) -> int:
    """A 1-10 salience score, per Generative Agents' retrieval model.

    A heuristic, and deliberately a transparent one rather than a second
    model call: scoring memories with an LLM would put a paid inference on
    the path of every tool call, which is exactly the kind of cost the
    broker's per-agent budget exists to keep off the hot path. The ranking
    it encodes is the defensible part - a failure outranks a success,
    something that changed the world outranks something that read it, and
    a reflection outranks the observations it was drawn from.
    """
    if memory_type == "reflection":
        return 8
    if memory_type == "plan":
        return 6
    if failed:
        return 7
    if tool and tool in CONSEQUENTIAL_TOOLS:
        return 5
    return 3


@dataclass
class _InFlightTurn:
    """One turn's raw material, accumulated until ExecutionSummary fires."""

    perception: str
    reasoning_chunks: list[str] = field(default_factory=list)
    text_chunks: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)

    def reasoning(self) -> str:
        """This turn's reasoning, from whichever stream carried it.

        Thinking deltas win when the provider emitted any: they are
        unambiguously reasoning. Text is the fallback, because on
        kimi-k2.5 it is the only stream reasoning has ever arrived on.
        """
        thought = " ".join(self.reasoning_chunks).strip()
        if thought:
            return thought
        return reasoning_from_text("".join(self.text_chunks))


def _pair_with_its_call(
    observations: list[dict[str, Any]], event: ToolExecution
) -> dict[str, Any] | None:
    """Which pending call this result belongs to, or None if no call is open.

    ENG-14. `ToolExecution` carries no tool_use_id in either phase - the
    result event carries neither the id nor even the params (thread.py's
    two emit sites) - so a correlation id is not available to the seam at
    all, and the review's fallback is order.

    Two calls to the same tool in one turn used to be paired
    NEWEST-unmatched-first, which is the reverse of the order results
    come back in: a parallel `say` to Mara and a `say` to Tobias had their
    outcomes swapped, so a refusal was remembered against the neighbour
    who had answered. Oldest-first restores the correspondence.

    `parallel_batch_id` is the one correlation field both phases carry and
    is deliberately NOT used to narrow this further: an observation's
    content records the tool and the result and never which call it was,
    so no narrowing beyond order is visible in what gets written, and a
    branch nothing can observe is a branch no test can defend.
    """
    for obs in observations:
        if obs["pending"] and obs["tool"] == event.tool_name:
            return obs
    return None


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_CONTENT_CHARS else text[: MAX_CONTENT_CHARS - 1] + "…"


class MemoryStreamWriter:
    """Turns one engine process's events into memory records.

    Writes newline-delimited JSON to `<home_root>/<agent_id>/memory_stream.jsonl`
    and, when a sink is attached, hands each record straight to it. The file
    is the durable record; the sink is how `world/wiring.py` gets the rows
    into the `memories` table. Either can be absent without breaking the
    other - a sim run with no town attached still keeps its own stream.
    """

    def __init__(self, agent_id: str, log_path: Path, *, sink: Any | None = None) -> None:
        self.agent_id = agent_id
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.sink = sink
        self.tick = 0
        self._current: _InFlightTurn | None = None
        self._seq = 0
        self._recent_observation_ids: list[int] = []

    def attach(self, emitter: EventEmitter) -> None:
        emitter.on(self._on_event)

    def start_turn(self, perception_text: str, tick: int | None = None) -> None:
        """Called right before the coordinator handles input, same as
        SessionLogWriter.start_turn - see pinned_boot.py."""
        if tick is not None:
            self.tick = tick
        self._current = _InFlightTurn(perception=perception_text)

    async def _on_event(self, event: Event) -> None:
        turn = self._current
        if turn is None:
            return
        if isinstance(event, ThinkingDelta):
            turn.reasoning_chunks.append(event.text)
        elif isinstance(event, TextDelta):
            if not turn.observations:
                turn.text_chunks.append(event.text)
        elif isinstance(event, ToolExecution):
            if event.phase == "call":
                turn.observations.append(
                    {"tool": event.tool_name, "params": event.params, "pending": True}
                )
            elif event.phase == "result":
                obs = _pair_with_its_call(turn.observations, event)
                if obs is not None:
                    obs["pending"] = False
                    obs["result"] = event.result
                    obs["failed"] = str(event.status).lower() in ("error", "failed", "denied")
        elif isinstance(event, ExecutionSummary):
            await self._close_turn()

    async def _close_turn(self) -> None:
        turn = self._current
        self._current = None
        if turn is None:
            return

        reasoning = turn.reasoning()
        written: list[dict[str, Any]] = []
        observation_ids: list[int] = []

        for obs in turn.observations:
            if obs.get("pending"):
                continue
            tool = obs.get("tool") or "unknown"
            if not obs.get("failed") and tool.split(":", 1)[-1] in UNREMARKABLE_WHEN_THEY_SUCCEED:
                continue
            verb = "tried and failed to" if obs.get("failed") else "did"
            content = f"I {verb} {tool}: {_clip(str(obs.get('result', '')))}"
            record = self._record(
                "observation",
                content,
                score_importance("observation", tool=tool, failed=bool(obs.get("failed"))),
            )
            observation_ids.append(record["seq"])
            written.append(record)

        if reasoning and _INTENT.search(reasoning):
            written.append(
                self._record("plan", _clip(reasoning), score_importance("plan"))
            )

        if reasoning and _BACKWARD_LOOKING.search(reasoning):
            cited = (observation_ids + self._recent_observation_ids)[:8]
            written.append(
                self._record(
                    "reflection",
                    _clip(reasoning),
                    score_importance("reflection"),
                    related=cited,
                )
            )

        self._recent_observation_ids = (observation_ids + self._recent_observation_ids)[:16]

        for record in written:
            self._append(record)
            if self.sink is not None:
                await self.sink(record)

    def _record(
        self,
        memory_type: str,
        content: str,
        importance: int,
        *,
        related: list[int] | None = None,
    ) -> dict[str, Any]:
        self._seq += 1
        return {
            "seq": self._seq,
            "agent_id": self.agent_id,
            "memory_type": memory_type,
            "content": content,
            "importance": importance,
            "related_memory_ids": related or [],
            "tick": self.tick,
            "written_at": time.time(),
        }

    def _append(self, record: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_memory_stream(log_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """The most recent `limit` records, most important first within the tail.

    Used by anything that wants an agent's own memory back without a
    database - the console, a test, or a restarted process reconstructing
    what it was doing.
    """
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-(limit * 4) :]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.sort(key=lambda r: (r.get("tick", 0), r.get("importance", 0)), reverse=True)
    return records[:limit]


MEMORY_STREAM_TOOL_NAME = "recall_memories"

MEMORY_STREAM_TOOL_ALLOW_RULE = PermissionRule(
    tool=MEMORY_STREAM_TOOL_NAME,
    pattern="",
    behavior=PermissionBehavior.ALLOW,
    reason="an agent reading back its own memories is always safe - read-only, self-scoped",
    source="engine",
)


def install_memory_stream_permissions(thread: Any) -> None:
    """Grant the recall_memories allow rule on an already-constructed thread -
    same idiom as session_log.install_session_log_permissions."""
    if MEMORY_STREAM_TOOL_ALLOW_RULE not in thread.session_rules:
        thread.session_rules.append(MEMORY_STREAM_TOOL_ALLOW_RULE)


MEMORY_KIND_LABEL = {"observation": "observed", "reflection": "reflected", "plan": "planned"}


def render_memories(log_path: Path, n: int = 12, kind: str | None = None) -> str:
    """The stream as text an agent can read, most recent and most important
    first. Importance is shown because it is the whole basis on which this
    ordering was chosen (Park et al., importance 1-10), and an agent that can
    see it can reason about what mattered rather than only what happened."""
    want = max(1, min(int(n), 50))
    records = read_memory_stream(log_path, limit=want * 3)
    if kind:
        prefix = kind.strip().lower()[:4]
        records = [r for r in records if str(r.get("memory_type", "")).lower().startswith(prefix)]
    records = records[:want]
    if not records:
        return "You have no memories recorded yet."
    lines = ["Your memories, most recent and most important first:"]
    for r in records:
        memory_type = str(r.get("memory_type", ""))
        label = MEMORY_KIND_LABEL.get(memory_type, memory_type or "noted")
        tick = r.get("tick")
        when = f"tick {tick}" if tick is not None else "earlier"
        lines.append(
            f"- [{when}, {label}, importance {r.get('importance', '?')}/10] "
            f"{r.get('content', '')}".rstrip()
        )
    return "\n".join(lines)


def build_memory_stream_tool(log_path: Path) -> ToolDef:
    """Registered into this engine process's own ToolRegistry by
    pinned_boot.py, alongside recall_session_log and the world:* tools."""

    async def handler(tool_input: dict[str, Any]) -> str:
        n = int(tool_input.get("count", 12))
        return render_memories(log_path, n=n, kind=tool_input.get("kind"))

    return ToolDef(
        name=MEMORY_STREAM_TOOL_NAME,
        description=(
            "Recall your own memories - what you have observed, what you have "
            "reflected on, and what you planned - before deciding what to do. "
            "Optionally pass 'count' (default 12) and 'kind' "
            "('observation', 'reflection' or 'plan') to narrow it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "How many memories to recall (default 12, max 50).",
                },
                "kind": {
                    "type": "string",
                    "description": "Narrow to 'observation', 'reflection' or 'plan'.",
                },
            },
        },
        handler=handler,
        source=ToolSource.INTERNAL,
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
    )
