"""A turn that meant to act and delivered nothing should say so.

TWO WAYS A TURN GOES QUIET, both measured by `app/proof/agility.py`
against the real chain, and both currently silent:

  1. The model finished with `tool_use` and the parser produced no tool
     call. `herald/provider.py` sets `current_tool_id` only on
     `content_block_start` and emits the call only at
     `content_block_stop`, so a provider that omits the opening event for
     a tool block has the whole call discarded - no exception, no log, no
     trace row - while `message_stop` still reports `tool_use`. The turn
     claims to have used tools it did not deliver.

     The engine is safe from this today only because the broker is
     generous: `translate.py` synthesises the `content_block_start`
     Bedrock omits for TEXT. Nothing puts back a missing start for a TOOL
     block, and nothing outside Bedrock is obliged to send one.

  2. The model wrote the call out as text in a markup nobody parses.
     `broker/kimi_tool_calls.py` recovers Kimi K2.5's private
     `<|tool_call_begin|>` spelling, and that file exists because of
     INFRA-07: Rosalind Vance answered the flour question seven times
     into a void across nine sessions of run 23. The next model will not
     use Kimi's spelling.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT DO.

It counts and it logs. It does not recover the call, because the call is
not recoverable: in case 1 the tool's NAME never crossed the wire, and in
case 2 guessing at an unknown markup is how you call the wrong verb with
the right-looking arguments. `kimi_tool_calls.py` draws the same line -
"a turn that did nothing is bad, and a turn that did SOMETHING ELSE is
worse."

It does not change control flow either. `engine_core/thread.py` already
ends the turn on `stop_reason != TOOL_USE or not turn_tool_calls`, so the
second clause has always handled the empty case correctly. The behaviour
was never wrong. What was wrong was the record: a turn that tried to act
and failed looked exactly like a resident that thought and chose not to,
and every instrument downstream - the acting-turn rate, the silent-turn
report, the whole question of whether residents are doing anything - read
it as the second.

WHY THE SEAM AND NOT THE PARSER. Recovering the call would need the
parser. Noticing that none arrived does not, and the seam is where this
project is allowed to work. `docs/decisions/LAW-1-MANIFEST.md` carries
the one edit that did need the frozen engine.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from src.herald.types import StopReason, StreamEvent

logger = logging.getLogger("kasukabe.sim.a_turn_that_tried_to_act")

# Structural shapes only. A resident writing "I will use world_move to get
# to the bakery" is prose and must not count; a resident emitting
# `functions.world_move:0 {...}` is a tool call in the wrong clothes.
# Every pattern needs a delimiter or a bracket, never a bare verb.
CALL_SHAPED = (
    re.compile(r"<\|[a-z_]*(?:tool|function)_call[a-z_]*\|>", re.I),
    re.compile(r"</?(?:tool_call|function_call|invoke|tool_use)\b", re.I),
    re.compile(r"\[\[\s*call\s*:", re.I),
    re.compile(r"\bfunctions\.[A-Za-z_]\w*\s*[(:{]"),
    re.compile(r'"(?:tool|function|tool_name)"\s*:\s*"[^"]+"\s*,\s*'
               r'"(?:arguments|parameters|input|params)"', re.I),
)


def looks_like_a_call(text: str) -> str:
    """The pattern that matched, or "" - the name is what gets logged."""
    for pattern in CALL_SHAPED:
        found = pattern.search(text)
        if found:
            return found.group(0)[:40]
    return ""


def notice_a_turn_that_tried_to_act(adapter: Any) -> Any:
    """Wrap an LLM adapter so a turn that delivered no call is counted.

    Delegates every other attribute down, the same proxy shape
    `resident_voice.speak_as_resident`,
    `bounded_monologue.end_the_turn_on_a_cut_monologue` and
    `half_written_tool_call.keep_the_turn_on_a_half_written_call` use.
    """
    return _TriedToActAdapter(adapter)


class _TriedToActAdapter:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        # Turns that ended `tool_use` and produced no call at all. A
        # provider that omits `content_block_start` for a tool block makes
        # every one of its turns this.
        self.turns_claiming_tools_that_sent_none = 0
        # Turns that produced no call and wrote something call-shaped.
        self.turns_that_wrote_a_call_as_text = 0
        # Turns whose `tool_use` stop reason was a contradiction and
        # was rewritten to the truth.
        self.stop_reasons_corrected = 0
        # The markup spellings seen, so the next `kimi_tool_calls.py` can
        # be written against evidence rather than a guess.
        self.spellings_seen: dict[str, int] = {}

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def stream_with_tools(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[StreamEvent]:
        calls = 0
        text: list[str] = []
        stop: StopReason | None = None

        async for event in self._inner.stream_with_tools(*args, **kwargs):
            if getattr(event, "tool_call", None) is not None:
                calls += 1
            elif event.type == "text" and event.text:
                text.append(event.text)

            reason = getattr(event, "stop_reason", None)
            if reason is not None:
                stop = reason
                # THE RECORD MUST NOT LIE. `tool_use` with no tool call is
                # not a stop reason, it is a contradiction: herald discarded
                # a block whose `content_block_start` never arrived, and the
                # turn is about to tell the trace it used tools it never
                # delivered. Every reader downstream - the acting-turn rate,
                # the silent-turn report, `run_checklist`'s scoring - takes
                # that at face value.
                #
                # The turn still ends exactly as it did: `thread.py` breaks
                # on `stop_reason != TOOL_USE or not turn_tool_calls`, and
                # the second clause was already true. Only the reason
                # written down changes, from a claim to the truth.
                if reason is StopReason.TOOL_USE and calls == 0:
                    self.stop_reasons_corrected += 1
                    event = StreamEvent(
                        type=event.type,
                        text=event.text,
                        tool_call=event.tool_call,
                        stop_reason=StopReason.END_TURN,
                        usage=event.usage,
                        thinking_block=event.thinking_block,
                        message_id=event.message_id,
                    )
            yield event

        if calls:
            return

        if stop is StopReason.TOOL_USE:
            self.turns_claiming_tools_that_sent_none += 1
            logger.warning(
                "the turn ended `tool_use` and carried no tool call at all "
                "(%d so far), so the stop reason was corrected to end_turn. "
                "A tool block whose `content_block_start` never arrived is "
                "discarded by herald/provider.py without an error, and the "
                "turn would otherwise report that it used tools.",
                self.turns_claiming_tools_that_sent_none,
            )

        spelling = looks_like_a_call("".join(text))
        if spelling:
            self.turns_that_wrote_a_call_as_text += 1
            self.spellings_seen[spelling] = self.spellings_seen.get(spelling, 0) + 1
            logger.warning(
                "the turn wrote something call-shaped (%r) and called nothing "
                "(%d so far). This is INFRA-07 in a spelling the broker does "
                "not recover: the turn was paid for, produced text, and "
                "changed nothing in the world.",
                spelling, self.turns_that_wrote_a_call_as_text,
            )
