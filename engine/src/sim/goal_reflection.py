"""Once a night, a resident looks at what it wanted and decides again.

ENG-24, the seam half. The world half (`app/world/tools/needs.py`,
`goal_lifecycle.py`, `primitives/goals.py`) gives a resident somewhere
to keep a goal and mechanical rules that move it: a pressing need
spawns one, a tool that succeeds credits one, a refusal writes a belief,
and the day boundary expires the short-horizon ones. All of that is
deterministic, and deliberately so - it is replayable from the trace,
and none of it asks a model anything.

But deterministic rules can only ever produce the goal the *situation*
implies. "Get something to eat" is a reflex, not an ambition, and a town
whose every goal was need-spawned would be a town of reflexes with a
goal system bolted on. §5.3's acceptance names the missing half:
*"sleep reflection posts `revise_goals` once per resident per night on
the LC adapter"*. This is that.

**Why the night, and why the LC adapter.** DESIGN.md section 1: agents
sleep ~8 game-hours, no interactive LLM calls, the learning pipeline
runs then. A reflection is exactly that shape of work - it looks
backward at a day that already happened - so it belongs where the
learning batch already is, on the background adapter, off the turn's
critical path (ENG-17's rule), and never during a turn a resident is
waiting on.

**Why once.** A reflection that ran per tick would be a second turn
loop at a second turn's price, and run 24's whole lesson was that a
drain which cannot afford itself is the same as no drain. One call per
resident per night, guarded by the game-day it last ran on, and skipped
entirely when the resident has nothing to reflect on.

**Why it writes through the world's own tools.** The engine has no
handle on `world.goals` and must not acquire one - `app/CONVENTIONS.md`
and the seam's whole shape depend on the world owning its state. So a
revision is a `set_goal` / `retire_goal` call POSTed like any other,
which means it is gated, priced, validated and traced exactly as a
resident's own call would be. A reflection cannot do anything a waking
resident could not do; it can only do it having thought about the day.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from src.herald.types import Message

logger = logging.getLogger(__name__)

MAX_REVISIONS = 3

MAX_REFLECTION_TOKENS = 400

REFLECTION_SYSTEM = (
    "You are thinking back over your own day while you sleep. You are not "
    "speaking to anyone and nobody will read this. Look at what you were "
    "trying to do, what actually happened, and what you have learned, and "
    "decide what you want tomorrow.\n\n"
    "Answer with a JSON array and nothing else. Each element is one of:\n"
    '  {"action": "keep", "goal_id": "..."}\n'
    '  {"action": "drop", "goal_id": "...", "why": "..."}\n'
    '  {"action": "new", "text": "...", "horizon": "today|week|life", '
    '"priority": 1-5}\n'
    f"At most {MAX_REVISIONS} elements. An empty array is a good answer if "
    "nothing should change. Do not explain yourself outside the JSON."
)


@dataclass
class ReflectionResult:
    """What one night's reflection did. Reported, never inferred."""

    ran: bool = False
    revisions: int = 0
    dropped: int = 0
    created: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_because: str | None = None


class GoalReflection:
    """One reflection per resident per game-day, at sleep onset.

    Holds the last game-day it ran on rather than a counter, because the
    guard has to survive a tick arriving twice for the same night - which
    the runner's dispatch does not promise it will not.
    """

    def __init__(self, engine: Any, *, enabled: bool = True) -> None:
        self._engine = engine
        self._enabled = enabled
        self._last_day: int | None = None

    @property
    def last_day(self) -> int | None:
        return self._last_day

    def due_for(self, game_day: int) -> bool:
        return self._enabled and self._last_day != game_day

    async def reflect(self, game_day: int, ticks_per_day: int = 144) -> ReflectionResult:
        """Look back, decide, and post the revisions to the world.

        Never raises. A reflection that fails is a night a resident did
        not think about its goals; it is not a reason to fail the sleep
        tick that carries the learning batch.
        """
        if not self.due_for(game_day):
            return ReflectionResult(skipped_because="already reflected on this day")
        self._last_day = game_day

        try:
            standing = await self._what_i_wanted()
        except Exception as exc:
            logger.warning("goal reflection could not read the world: %s", exc)
            return ReflectionResult(errors=[f"read failed: {exc}"])

        if not standing.get("goals") and not standing.get("needs"):
            return ReflectionResult(skipped_because="nothing to reflect on")

        try:
            decided = await self._decide(standing)
        except Exception as exc:
            logger.warning("goal reflection call failed: %s", exc)
            return ReflectionResult(errors=[f"call failed: {exc}"])

        return await self._apply(decided)

    async def _what_i_wanted(self) -> dict[str, Any]:
        """Ask the world what this resident is carrying.

        Through `recall_goals`, a read-only world tool, for the same
        reason the writes go through tools: the engine does not get a
        private window onto world state.
        """
        raw = await self._engine.world.call_tool("recall_goals", {})
        if raw.startswith("Error:"):
            raise RuntimeError(raw)
        try:
            return json.loads(raw)
        except ValueError:
            raise RuntimeError(f"recall_goals returned non-JSON: {raw[:200]}")

    async def _decide(self, standing: dict[str, Any]) -> list[dict[str, Any]]:
        """One call on the LC adapter. Returns whatever parsed."""
        summary = json.dumps(standing, ensure_ascii=False)[:4000]
        response = await self._engine.lc_adapter.generate_with_tools(
            messages=[Message(role="user", content=f"Your day:\n{summary}")],
            system=[{"type": "text", "text": REFLECTION_SYSTEM}],
            max_output_tokens=MAX_REFLECTION_TOKENS,
            skip_cache_write=True,
        )
        return _parse_revisions(response.text)

    async def _apply(self, decided: list[dict[str, Any]]) -> ReflectionResult:
        result = ReflectionResult(ran=True)
        for revision in decided[:MAX_REVISIONS]:
            action = str(revision.get("action") or "").lower()
            if action == "keep":
                continue
            if action == "drop":
                goal_id = revision.get("goal_id")
                if not goal_id:
                    result.errors.append("drop with no goal_id")
                    continue
                said = await self._engine.world.call_tool(
                    "retire_goal",
                    {"goal_id": goal_id, "done": False, "why": revision.get("why") or "slept on it"},
                )
                if said.startswith("Error:"):
                    result.errors.append(said[:120])
                else:
                    result.dropped += 1
                    result.revisions += 1
            elif action == "new":
                text = str(revision.get("text") or "").strip()
                if not text:
                    result.errors.append("new with no text")
                    continue
                payload: dict[str, Any] = {"text": text[:160]}
                horizon = revision.get("horizon")
                if horizon in {"now", "today", "week", "life"}:
                    payload["horizon"] = horizon
                priority = revision.get("priority")
                if isinstance(priority, int) and 1 <= priority <= 5:
                    payload["priority"] = priority
                said = await self._engine.world.call_tool("set_goal", payload)
                if said.startswith("Error:"):
                    result.errors.append(said[:120])
                else:
                    result.created += 1
                    result.revisions += 1
            else:
                result.errors.append(f"unknown action {action!r}")
        return result


def _parse_revisions(text: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of a reflection, tolerantly.

    Models fence JSON in ```json blocks about half the time, and wrap it
    in a sentence of preamble the rest - neither is worth a refusal. The
    resident thought, and the thought should not be thrown away over
    punctuation.

    Slicing between the first `[` and the last `]` handles both without
    a special case for the fence: a ``` block's backticks fall outside
    that span. That is worth saying because the special case WAS written
    first, and reverting it changed no test - a guard that cannot fail
    is not a guard, it is a branch nothing covers.

    Anything that is not a list of objects is discarded. Guessing at a
    shape is how a reflection quietly starts doing nothing.
    """
    body = text.strip()
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(body[start : end + 1])
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]
