"""A tool call cut off mid-JSON must not take the whole turn with it.

THE BUG, measured. The 50-resident run of 2026-09-16 lost 41 turns across
33 of its 50 residents, each one a Bedrock call that answered
`HTTP/1.1 200 OK` and then produced nothing:

    ERROR:src.engine_core.coordinator:Thread execution failed for task
      kasukabe-agent-a0204: Unterminated string starting at: line 1 column 241

`herald/provider.py` accumulates a streamed tool call's arguments across
`input_json_delta` events and parses them at `content_block_stop` with a
bare `json.loads`. `sim/config.py` caps every response at 768 tokens, so a
response that runs out while those arguments are still being written
leaves incomplete JSON. The parse raises out of the generator, past
`thread.py`'s agent loop, to the coordinator, which fails the task; `/tick`
answers 500.

The severity is the TURN, not the call: the exception escapes the
generator, so tool calls the model already completed in that same response
die with it.

`herald/` is frozen under ROADMAP Law 1, so the unguarded parse is written
up (`docs/decisions/A-HALF-WRITTEN-TOOL-CALL-KILLS-A-TURN.md`) rather than
patched. What the seam does is refuse to lose the turn to it.
"""

from __future__ import annotations

import json

import pytest

from src.herald.types import StopReason, StreamEvent, ToolCall
from src.sim.half_written_tool_call import keep_the_turn_on_a_half_written_call

MODEL_ID = "moonshotai.kimi-k2.5"


class _ScriptedAdapter:
    """Shaped like a herald Provider: yields events, then optionally raises.

    `raises` reproduces the real failure shape - the provider raises from
    INSIDE the generator, after some events have already been yielded,
    which is exactly why the completed calls were being lost too.
    """

    def __init__(self, events, raises: BaseException | None = None):
        self._events = events
        self._raises = raises
        self.model = MODEL_ID
        self.calls = 0

    async def stream_with_tools(self, *args, **kwargs):
        self.calls += 1
        for event in self._events:
            yield event
        if self._raises is not None:
            raise self._raises


def _cut_json() -> json.JSONDecodeError:
    """The real exception, raised the way `json.loads` raises it."""
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads('{"content": "two potatoes pl')
    return caught.value


@pytest.mark.asyncio
async def test_a_cut_off_tool_call_ends_the_turn_instead_of_killing_it():
    wrapped = keep_the_turn_on_a_half_written_call(
        _ScriptedAdapter(
            [StreamEvent(type="text", text="I should ask Farid.", message_id="m1")],
            raises=_cut_json(),
        )
    )

    events = [event async for event in wrapped.stream_with_tools([])]

    assert [e.type for e in events] == ["text", "stop"], (
        "the stream did not close cleanly; the turn would still die"
    )
    assert events[-1].stop_reason is StopReason.END_TURN
    assert events[-1].message_id == "m1", (
        "the terminal stop lost the message id, so the session log keys the "
        "turn as a different fault than the one that happened"
    )
    assert wrapped.tool_calls_half_written == 1


@pytest.mark.asyncio
async def test_the_work_already_done_survives():
    """The point of the fix. A resident that harvested and walked before its
    third call was cut off keeps the harvest and the walk."""
    wrapped = keep_the_turn_on_a_half_written_call(
        _ScriptedAdapter(
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id="t1", name="world:harvest", input={}),
                ),
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(id="t2", name="world:move", input={}),
                ),
            ],
            raises=_cut_json(),
        )
    )

    events = [event async for event in wrapped.stream_with_tools([])]
    called = [e.tool_call.name for e in events if e.type == "tool_call"]

    assert called == ["world:harvest", "world:move"], (
        "completed tool calls were lost with the malformed one - the exact "
        "damage this exists to stop"
    )
    assert events[-1].type == "stop"


@pytest.mark.asyncio
async def test_a_clean_stream_is_untouched():
    """The common case must pass through byte for byte, counter at zero."""
    clean = [
        StreamEvent(type="tool_call", tool_call=ToolCall(id="t1", name="world:say", input={})),
        StreamEvent(type="stop", stop_reason=StopReason.END_TURN, message_id="m9"),
    ]
    wrapped = keep_the_turn_on_a_half_written_call(_ScriptedAdapter(clean))

    events = [event async for event in wrapped.stream_with_tools([])]

    assert events == clean
    assert wrapped.tool_calls_half_written == 0


@pytest.mark.asyncio
async def test_a_real_fault_still_travels():
    """Narrow on purpose. Swallowing a transport error or a TypeError would
    trade 41 visible dead turns for an invisible broken engine."""
    wrapped = keep_the_turn_on_a_half_written_call(
        _ScriptedAdapter([], raises=RuntimeError("the socket died"))
    )

    with pytest.raises(RuntimeError, match="the socket died"):
        [event async for event in wrapped.stream_with_tools([])]

    assert wrapped.tool_calls_half_written == 0


@pytest.mark.asyncio
async def test_the_wrapper_hides_nothing_it_wraps():
    """A proxy that hid the adapter's model id or its non-streaming methods
    would break the engine in ways nothing in this package would notice."""
    inner = _ScriptedAdapter([])
    wrapped = keep_the_turn_on_a_half_written_call(inner)

    assert wrapped.model == MODEL_ID
    assert wrapped._inner is inner


def test_the_seam_is_where_the_half_written_fix_lives():
    """ROADMAP Law 1: `src/herald/` does not change. If the half-written
    fix ever moves into the provider, the run stops being a statement about
    the product.

    THE PROVIDER HAS BEEN FORKED, DELIBERATELY, AND THIS TRIPWIRE CAUGHT IT.
    The operator overrode Law 1 on `evals/proving-the-town`, and
    `docs/decisions/LAW-1-MANIFEST.md` carries every edit with its evidence
    and its revert. Entry 2 replaced three scalars - `current_tool_id`,
    `current_tool_name`, `current_tool_json` - with a dict keyed by the
    content-block index, because `event["index"]` was read nowhere and two
    interleaved tool blocks concatenated their arguments into one string.

    What this test guards is UNCHANGED, and it is the important half: the
    parse is still unguarded. `keep_the_turn_on_a_half_written_call` is
    still the only thing standing between a truncated argument and a dead
    turn. A `try` around this line would mean the seam wrapper and its
    written-up finding need revisiting together, and that is still what
    this assertion is here to notice.
    """
    from pathlib import Path

    provider = Path(__file__).resolve().parents[1] / "src" / "herald" / "provider.py"
    text = provider.read_text(encoding="utf-8")
    parse_line = 'input=json.loads(slot["json"]) if slot["json"] else {},'

    assert parse_line in text, (
        "the frozen provider's unguarded parse has changed again - if that "
        "was deliberate it is another fork, and the seam wrapper plus its "
        "written-up finding should be revisited together. Check "
        "docs/decisions/LAW-1-MANIFEST.md before changing this line."
    )
    assert "try:" not in text.split(parse_line)[0].rsplit("\n", 3)[-1], (
        "the parse looks guarded now - if the fix moved into the frozen "
        "provider, half_written_tool_call.py is dead code pretending to work"
    )
