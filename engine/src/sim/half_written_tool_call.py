"""A tool call cut off mid-JSON ends the turn; it does not kill it.

The 50-resident run of 2026-09-16 lost **41 turns** this way, spread
across **33 of its 50 residents** - every one a Bedrock call that was
billed, answered `HTTP/1.1 200 OK` at the broker, and then produced
nothing at all:

    INFO:httpx:HTTP Request: POST http://localhost:8200/v1/messages "HTTP/1.1 200 OK"
    ERROR:src.engine_core.coordinator:Thread execution failed for task
      kasukabe-agent-a0204: Unterminated string starting at: line 1 column 241

WHERE IT BREAKS. `herald/provider.py` accumulates a streamed tool call's
arguments across `input_json_delta` events into `current_tool_json`, and
at `content_block_stop` does:

    input=json.loads(current_tool_json) if current_tool_json else {},

unguarded. A response that stops while those arguments are still being
written leaves incomplete JSON, `json.loads` raises `JSONDecodeError`
out of the generator, and the exception travels all the way up through
`thread.py`'s agent loop to the coordinator, which fails the whole task.
`/tick` answers 500 and the world logs a dispatch failure.

THE SEVERITY IS THE WHOLE TURN, NOT THE ONE CALL. The exception escapes
the generator, so every tool call the model completed EARLIER in that
same response - already yielded, already valid - dies with it. A resident
that harvested, walked, and then began a malformed third call loses the
harvest and the walk too.

WHY THE CAP IS THE CAUSE. `sim/config.py` holds every response to
SIM_MAX_RESPONSE_TOKENS (768). `sim/bounded_monologue.py` already handles
the half of this problem where the cap lands in prose, and says in its own
exclusions that it deliberately does not touch "a stream that produced a
tool call and then hit the cap". This is the case it left: the cap landing
INSIDE the arguments, before the call was ever complete enough to yield.
The offsets in the run vary from column 8 to column 1,513, which is what a
cap falling wherever the model happened to be looks like - not a fixed
truncation point.

WHAT THIS DOES. Catches `JSONDecodeError` coming out of the provider's
stream and closes the stream with an ordinary `end_turn` stop instead of
letting it propagate. Every event already yielded stands, so the turn
keeps the work it really did and the loop takes its normal end-of-turn
path. The malformed call is the only thing lost, which is the only thing
that was never usable.

WHY NOT FIX IT IN HERALD. `src/herald/` is frozen under ROADMAP Law 1,
and the law is explicit about the alternative: "If a round appears to need
a change under `engine/src/` outside `sim/`, that is a finding about the
engine and gets written up rather than patched around." So the unguarded
parse is written up as an engine finding
(`docs/decisions/A-HALF-WRITTEN-TOOL-CALL-KILLS-A-TURN.md`) and this file
does not touch it. What it does is refuse to let the town lose a turn to
it - the same seam technique `bounded_monologue.py` uses on the stop
reason and `resident_voice.py` uses on the history.

NARROW ON PURPOSE. Only `json.JSONDecodeError`, and only around the
stream. A `TypeError` or a transport failure is a real fault and must
still travel; swallowing those would trade 41 visible dead turns for an
invisible broken engine, which is the worse bargain.

Counted (`tool_calls_half_written`) and logged, for the same reason
`bounded_monologue` counts its rewrites: a run losing many of these is
telling the operator the 768-token cap is too low for the tools it is
asking residents to call, and that is a decision, not a defect.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from src.herald.types import StopReason, StreamEvent

logger = logging.getLogger("kasukabe.sim.half_written_tool_call")


def keep_the_turn_on_a_half_written_call(adapter: Any) -> Any:
    """Wrap an LLM adapter so a truncated tool call ends the turn.

    Delegates every other attribute down, the same proxy shape
    `resident_voice.speak_as_resident` and
    `bounded_monologue.end_the_turn_on_a_cut_monologue` use.
    """
    return _HalfWrittenToolCallAdapter(adapter)


class _HalfWrittenToolCallAdapter:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        # How many turns this resident kept that the provider's unguarded
        # parse would have killed. Read by tests; worth a run-report line.
        self.tool_calls_half_written = 0

    def __getattr__(self, item: str) -> Any:
        # Only reached for attributes this class does not define, so the
        # wrapper adds `stream_with_tools` and hides nothing.
        return getattr(self._inner, item)

    async def stream_with_tools(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        """Pass the stream through; close it cleanly if the JSON is cut.

        The terminal `stop` carries the last `message_id` seen rather than
        an empty one: the session log keys a turn by it, and a turn that
        ended with no id reads as a different fault than the one that
        happened.
        """
        message_id = ""
        try:
            async for event in self._inner.stream_with_tools(*args, **kwargs):
                seen = getattr(event, "message_id", "")
                if seen:
                    message_id = seen
                yield event
        except json.JSONDecodeError as exc:
            self.tool_calls_half_written += 1
            logger.warning(
                "a tool call's arguments were cut off mid-JSON (%s at char %d): "
                "ending the turn on what it already did, instead of losing the "
                "whole turn to the provider's unguarded parse "
                "(herald/provider.py content_block_stop; %d so far)",
                exc.msg, exc.pos, self.tool_calls_half_written,
            )
            yield StreamEvent(
                type="stop",
                stop_reason=StopReason.END_TURN,
                message_id=message_id,
            )
