"""Session log: one real, structured record per turn, for every engine
process - The 250 section 32 / AGENT_LIFE_FRAMEWORK.md section 32: "Every
engine spinned off should have sessions log - irrespective of what the
agent is doing, it has to follow it."

Four required fields, taken verbatim from that spec:
  1. input received      - what triggered this turn
  2. model reasoning      - what led to the goal/work-progress update
  3. actions              - tool calls / skill replies / communication
  4. done status           - confirmation the turn completed, with what changed

This module is entirely inside `engine/src/sim/*` - the changeable seam
(ROADMAP.md's Law 1) - and touches nothing under `src/engine_core/`,
`src/memory/`, `src/herald/`, or `src/learning_centre/`. It works by
subscribing to `EventEmitter` (src/engine_core/events.py), a public
pub/sub interface the frozen engine already emits `ThinkingDelta`,
`ToolExecution`, and `ExecutionSummary` events through for its own
observability - `.on(callback)` is a documented extension point, not an
edit to that file.

Two consumers, one mechanism (section 32.3's "one tool, one mechanism,
for two related situations"):
  - A fresh context (a restarted process, a compacted context window)
    has no memory of what it was doing a moment ago.
  - An agent that hasn't crashed but has drifted - lost the thread of
    its own reasoning - needs the same thing: a look back at its own
    recent turns.

Both are served by `recall_session_log`, one more ToolDef registered into
this engine's own ToolRegistry (world_tools.py's `build_world_tool_registry`
is a sibling example of exactly this pattern: engine-side tools that call
back into engine-owned state, not world state).

A third consumer arrived with ENG-09, and it does not wait to be called.
The conversation store keeps, as the resident's own assistant turn, the
narration and nothing else, so every earlier self a resident meets in its
context is a paragraph with no action in it. `render_action_record` turns
the turn this writer is holding right now into a few lines of acts, and
`bounded_history.py` stores THAT as the assistant turn instead. The
accumulator here is the only place in the process that knows, mid-turn,
what the turn has done - the events have already been folded in and the
record has not been flushed yet - which is why the answer to "what did I
just do" is asked of the session log rather than assembled a second time.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.engine_core.events import (
    Event,
    EventEmitter,
    ExecutionSummary,
    TextDelta,
    ThinkingDelta,
    ToolExecution,
)
from src.sandbox.permissions import PermissionBehavior, PermissionRule, ToolSource
from src.utility_directory.tool_runtime import ToolDef

SESSION_LOG_FILENAME = "session_log.jsonl"

SESSION_LOG_TOOL_NAME = "recall_session_log"

SESSION_LOG_TOOL_ALLOW_RULE = PermissionRule(
    tool=SESSION_LOG_TOOL_NAME,
    pattern="",
    behavior=PermissionBehavior.ALLOW,
    reason="an agent reading back its own session log is always safe - read-only, self-scoped",
    source="engine",
)


ACTION_RECORD_MAX_ACTIONS = 12
ACTION_RECORD_MAX_PARAM_CHARS = 100
ACTION_RECORD_MAX_RESULT_CHARS = 160

FAILED_STATUSES = frozenset({"error", "failed", "denied"})

RECORD_HEADING = "RECORD OF THE TURN THAT JUST ENDED (written by the world, not by you):"

SILENT_TURN_RECORD = (
    f"{RECORD_HEADING} "
    "no tool was called, so nothing about the town changed. The turn was "
    "billed anyway, and whatever had been decided was not carried out."
)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_params(params: dict[str, Any]) -> str:
    """`to=marta, content="Morning."` - the arguments, as they were passed.

    Which arguments a resident used is half of what an act was: "I said" is
    not a memory, "I said that to Marta" is. Rendered as key=value rather
    than as the JSON that went on the wire because this is read by a model
    as prose about itself, not parsed.
    """
    if not isinstance(params, dict) or not params:
        return ""
    parts = []
    for key, value in params.items():
        if isinstance(value, str):
            rendered = '"' + _clip(value, ACTION_RECORD_MAX_PARAM_CHARS) + '"'
        else:
            rendered = _clip(json.dumps(value, default=str), ACTION_RECORD_MAX_PARAM_CHARS)
        parts.append(f"{key}={rendered}")
    return ", ".join(parts)


def render_action_record(actions: list[dict[str, Any]]) -> str:
    """One turn's acts, in the first person, in the order they happened.

    An action with no `result` key is one whose result event never arrived -
    a call the turn was cut off in the middle of. It is recorded as
    attempted rather than dropped, because a resident that was interrupted
    mid-act needs to know it started.
    """
    if not actions:
        return SILENT_TURN_RECORD

    shown = actions[:ACTION_RECORD_MAX_ACTIONS]
    lines = [RECORD_HEADING]
    for action in shown:
        head = str(action.get("tool", "?"))
        params = _render_params(action.get("params") or {})
        line = f"- {head}({params})"
        if "result" not in action:
            lines.append(line + " - I started this and did not see it finish.")
            continue
        outcome = _clip(action.get("result") or "", ACTION_RECORD_MAX_RESULT_CHARS)
        if str(action.get("status", "")).lower() in FAILED_STATUSES:
            lines.append(f"{line} - this failed: {outcome}")
        else:
            lines.append(f"{line} -> {outcome}")
    if len(actions) > len(shown):
        lines.append(f"- and {len(actions) - len(shown)} further action(s).")
    return "\n".join(lines)


_active_session_log: "SessionLogWriter | None" = None


def active_session_log() -> "SessionLogWriter | None":
    """The writer `attach` last bound to this process's emitter."""
    return _active_session_log


def install_session_log_permissions(thread: Any) -> None:
    """Grant the recall_session_log allow rule on an already-constructed
    thread - same idiom as world_tools.install_permissions."""
    if SESSION_LOG_TOOL_ALLOW_RULE not in thread.session_rules:
        thread.session_rules.append(SESSION_LOG_TOOL_ALLOW_RULE)


@dataclass
class _InFlightTurn:
    input_text: str
    reasoning_chunks: list[str] = field(default_factory=list)
    output_chunks: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)


class SessionLogWriter:
    """Accumulates one turn's events (subscribed via `attach`) and writes
    exactly one JSON line to `<home_root>/<agent_id>/session_log.jsonl`
    when that turn's `ExecutionSummary` fires - the engine's own signal
    that a task has completed, so this never has to guess where a turn
    ends. `start_turn` must be called (by `SimEngineState.send_tick`,
    pinned_boot.py) right before `coordinator.handle_input(...)`, so the
    accumulator has somewhere to put the input text and knows a new turn
    has begun.
    """

    def __init__(self, agent_id: str, log_path: Path) -> None:
        self.agent_id = agent_id
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._current: _InFlightTurn | None = None
        self.last_summary: dict[str, Any] | None = None

    def attach(self, emitter: EventEmitter) -> None:
        global _active_session_log

        emitter.on(self._on_event)
        _active_session_log = self

    def start_turn(self, input_text: str) -> None:
        self._current = _InFlightTurn(input_text=input_text)

    def action_record(self) -> str | None:
        """ENG-09. The turn in flight, as acts rather than as narration.

        `None` - meaning "I have nothing to say about this save" - when no
        turn is in flight, so a caller that would otherwise rewrite an
        assistant message leaves it exactly as the engine wrote it. That is
        not the same answer as a turn which is in flight and has done
        nothing; that one is a fact worth storing, and it is stored.

        Read mid-turn. Frozen `thread.py:3098-3108` saves the conversation
        rows BEFORE it emits the `ExecutionSummary` that flushes this
        accumulator (`:3142`), so `_current` is still the turn being saved.
        """
        if self._current is None:
            return None
        return render_action_record(self._current.actions)

    async def _on_event(self, event: Event) -> None:
        if self._current is None:
            return
        if isinstance(event, ThinkingDelta):
            self._current.reasoning_chunks.append(event.text)
        elif isinstance(event, TextDelta):
            self._current.output_chunks.append(event.text)
        elif isinstance(event, ToolExecution):
            if event.phase == "call":
                self._current.actions.append(
                    {"tool": event.tool_name, "params": event.params, "source": event.source}
                )
            elif event.phase == "result":
                for action in reversed(self._current.actions):
                    if action["tool"] == event.tool_name and "result" not in action:
                        action["result"] = event.result
                        action["status"] = event.status
                        break
        elif isinstance(event, ExecutionSummary):
            self._flush(event)

    def _flush(self, summary: ExecutionSummary) -> None:
        if self._current is None:
            return
        entry = {
            "agent_id": self.agent_id,
            "recorded_at": time.time(),
            "input": self._current.input_text,
            "reasoning": "".join(self._current.reasoning_chunks),
            "output": "".join(self._current.output_chunks),
            "actions": self._current.actions,
            "done_status": {
                "status": summary.status,
                "exit_reason": summary.exit_reason,
                "tool_calls_count": summary.tool_calls_count,
                "num_turns": summary.num_turns,
                "model_id": summary.model_id,
            },
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self.last_summary = entry["done_status"]
        self._current = None


def read_recent_turns(log_path: Path, n: int = 5) -> list[dict[str, Any]]:
    """Last n turn records, oldest first. Empty for a brand-new agent
    (no file yet) rather than an error - a first turn has nothing to
    recall, which is a normal state, not a fault."""
    if not log_path.exists():
        return []
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tail = lines[-n:] if n > 0 else lines
    return [json.loads(line) for line in tail]


def render_recovery_context(log_path: Path, n: int = 5) -> str:
    """The text a fresh context - or an agent that's lost track of its
    own reasoning - gets handed back. Section 32.3's "one tool, one
    mechanism" for both situations: whichever called this, the answer is
    the same recent history, read the same way.
    """
    turns = read_recent_turns(log_path, n=n)
    if not turns:
        return "No prior session log yet - this is this agent's first recorded turn."
    lines = [f"Your last {len(turns)} recorded turn(s), most recent last:"]
    for i, turn in enumerate(turns, start=1):
        lines.append(f"\nTurn {i}:")
        lines.append(f"  You perceived: {turn['input']}")
        if turn.get("reasoning"):
            lines.append(f"  You reasoned: {turn['reasoning']}")
        if turn.get("output"):
            lines.append(f"  You said: {turn['output']}")
        if turn.get("actions"):
            action_summaries = ", ".join(a["tool"] for a in turn["actions"])
            lines.append(f"  You acted: {action_summaries}")
        status = turn.get("done_status", {}).get("status", "unknown")
        lines.append(f"  Turn ended: {status}")
    return "\n".join(lines)


def build_session_log_tool(log_path: Path) -> ToolDef:
    """The recovery tool itself - registered directly into this engine
    process's own ToolRegistry (pinned_boot.py does this, alongside the
    world:* tools world_tools.py registers), callable by the model
    exactly like any other tool.
    """

    async def handler(tool_input: dict[str, Any]) -> str:
        n = int(tool_input.get("turns", 5))
        return render_recovery_context(log_path, n=n)

    return ToolDef(
        name="recall_session_log",
        description=(
            "Read back your own recent turns - what you perceived, reasoned, and "
            "did - if you've lost track of what you were doing or your context "
            "started fresh. Optionally pass 'turns' (default 5)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "turns": {
                    "type": "integer",
                    "description": "How many recent turns to recall (default 5).",
                }
            },
        },
        handler=handler,
        source=ToolSource.INTERNAL,
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
    )
