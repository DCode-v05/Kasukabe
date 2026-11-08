"""A monologue cut off at the token cap ends the turn; it does not buy a
second model call.

ENG-21. `src/sim/config.py` caps every single model response at
SIM_MAX_RESPONSE_TOKENS (768) so a resident cannot spend a tick writing
four thousand tokens of prose. `app/world/call_budget.py` separately caps
how MANY calls a tick may make, with a floor of two
(`MIN_CALLS_PER_TICK = 2`) so that even a penniless resident gets one call
to act and one to react. The two caps were built for different reasons and
nobody had put them next to each other.

`thread.py`'s agent loop is `for turn in range(state.max_turns)` and the
seam passes the world's call budget in as `max_turns`
(`pinned_boot.SimEngineState.send_tick`). Inside that loop, MAX_TOKENS
recovery (`thread.py` around lines 4104-4160, frozen) does:

    if turn_stop_reason == StopReason.MAX_TOKENS:
        ...
        if continuation_count < MAX_CONTINUATIONS:   # MAX_CONTINUATIONS = 3
            messages = [... "Output token limit hit. Resume directly ..."]
            continuation_count += 1
            continue

`continue` is a fresh iteration of the SAME bounded loop, and
`state.record_turn_usage` has already incremented `state.turns`. So:

    call 1  monologue reaches 768 tokens -> stop_reason=max_tokens
    call 2  the continuation prompt -> the resident keeps talking
    ------  budget of 2 exhausted, loop falls out of `for`
            exit_reason = max_turns, num_turns = 2

`pinned_boot.send_tick` reads that back into `last_turn_hit_budget`,
`agent_adapter` returns it on the /tick response, and
`world/retrieval_depth.py` puts `call_budget.spent_line(2)` into the next
perception: **"You ran out of energy for thinking last turn after 2
steps."** The resident is told it exhausted its turn having attempted
NOTHING - no tool call was ever made, both "steps" were the same
uninterrupted sentence. Two calls were billed, the world's floor of two
was consumed, and the town's physics never heard from that resident.

WHAT THIS DOES. Rewrites `stop_reason=max_tokens` to `end_turn` on its way
back from the provider, but ONLY when no tool call came out of that same
stream. The frozen recovery block is then never entered - the loop takes
its ordinary end-of-turn path, `state.exit_reason` stays COMPLETED, and the
world's floor of two means two USEFUL calls. This is the review's own
prescribed fix ("cap continuations at 0 in the sim (seam adapter returns
`end_turn` on `max_tokens` when no tool call was in flight)").

WHY THE ADAPTER AND NOT A SETTING. `MAX_CONTINUATIONS = 3` is a local
variable in `ExecutionThread.agent_loop`, not a setting, not a field, not
an option - there is no knob. `thread.py` is frozen under ROADMAP Law 1.
The adapter is constructed in `pinned_boot.py`, so it can be wrapped there:
the same technique `resident_voice.py` uses on the summary prompt and
`tool_surface.py` uses on the tool registry.

WHAT IT DELIBERATELY DOES NOT TOUCH.

  * A stream that produced a tool call and then hit the cap. The engine's
    continuation is doing real work there - it tombstones the interrupted
    results and lets the model finish what it started - and a call spent
    finishing an action is exactly what the budget is FOR.

  * MAX_TOKENS escalation. That branch is already unreachable in this
    topology: it is gated on `settings.max_tokens == Settings.max_tokens`
    (4096) and `sim/config.py` sets MAX_TOKENS to 768, so an escalation
    retry never fires here and the continuation is the whole cost.

  * `generate_with_tools`. Only `thread.py:3815`'s `stream_with_tools`
    feeds the bounded agent loop; the two `generate_with_tools` callers
    (agent_summarizer, context_engineering) are one-shot summary calls with
    no continuation loop behind them.

THE ONE CASE IT CANNOT SEE. If the cap lands in the MIDDLE of a tool_use
block, the provider never emits a `tool_call` event for it
(`herald/provider.py` yields the tool call at `content_block_stop`, which
never arrives), so this reads it as a monologue and ends the turn. The
frozen engine is equally blind - its own `if turn_tool_calls:` tombstone
branch is skipped for exactly the same reason - so what is lost is one
continuation that would have re-attempted an invisible call. At 768 output
tokens a truncated tool_use block means the model wrote ~3,000 characters
of prose first, which is the case this exists to stop.

The rewrite is counted (`monologues_cut`) and logged, because a run that
truncates half its turns is telling the operator the 768 cap is too low,
and after the rewrite the session log's `last_model_stop` reads `end_turn`
like any other finished turn.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from src.herald.types import StopReason, StreamEvent

logger = logging.getLogger("kasukabe.sim.bounded_monologue")


def end_the_turn_on_a_cut_monologue(adapter: Any) -> Any:
    """Wrap an LLM adapter so a truncated monologue ends the turn.

    Delegates every other attribute to the adapter it wraps (same proxy
    shape as `resident_voice.speak_as_resident`), so the model id, the
    herald tool cache and the non-streaming methods are all still reachable
    through it.
    """
    return _NoContinuationAdapter(adapter)


class _NoContinuationAdapter:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        # How many turns this resident ended because its own sentence was
        # cut off. Read by tests; worth reading in a run report too.
        self.monologues_cut = 0

    def __getattr__(self, item: str) -> Any:
        # Only reached for attributes this class does not define, so the
        # wrapper adds `stream_with_tools` and hides nothing.
        return getattr(self._inner, item)

    async def stream_with_tools(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        """Pass the stream through, rewriting one event at most.

        `herald/provider.py` yields every completed `tool_call` before the
        single terminal `stop` event, so by the time the stop arrives this
        already knows whether anything was in flight.
        """
        a_tool_call_was_in_flight = False
        async for event in self._inner.stream_with_tools(*args, **kwargs):
            if event.type == "tool_call":
                a_tool_call_was_in_flight = True
            elif (
                event.type == "stop"
                and event.stop_reason == StopReason.MAX_TOKENS
                and not a_tool_call_was_in_flight
            ):
                self.monologues_cut += 1
                logger.info(
                    "monologue cut at the response cap with no tool call in "
                    "flight: ending the turn instead of spending a "
                    "continuation on it (ENG-21; %d so far)",
                    self.monologues_cut,
                )
                yield StreamEvent(
                    type="stop",
                    stop_reason=StopReason.END_TURN,
                    message_id=event.message_id,
                )
                continue
            yield event
