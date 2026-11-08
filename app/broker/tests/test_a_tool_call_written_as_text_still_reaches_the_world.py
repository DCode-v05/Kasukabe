"""Kimi's text-form tool calls become real ones.

`INFRA-07`. Kimi K2.5 sometimes emits its own tool-call syntax as PLAIN
TEXT rather than as a Bedrock `toolUse` block:

    <|tool_call_begin|> functions.world_move:0 <|tool_call_argument_begin|>
    {"destination_location_id": "Kasukabe Bakery"} <|tool_call_end|>

`herald` only acts on `tool_use` blocks. So the turn was billed in full,
produced a long deliberation, and changed nothing in the world. Rosalind
Vance's nine sessions in run 23 went this way: she answered the flour
question seven times into a void, and the one question that stalled the
town's bread supply was answered and never delivered.

The broker is the only place this can be fixed. `herald/provider.py` is
frozen under ROADMAP Law 1, and by the time a `StreamEvent` reaches
`engine/src/sim/` herald has already classified these deltas as text. The
broker already rewrites this stream - it synthesises the
`content_block_start` events Bedrock omits - so it sees the raw deltas
and is allowed to change them.

The markers below are copied from run 23's own session logs, not guessed.
"""
from __future__ import annotations

import json

import pytest

from broker.kimi_tool_calls import MarkupBuffer
from broker.translate import anthropic_sse_events_from_converse_stream


async def _stream(chunks: list[str], stop_reason: str = "end_turn"):
    """A Bedrock stream whose text deltas carry `chunks`."""
    yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
    for chunk in chunks:
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": chunk}}}
    yield {"contentBlockStop": {"contentBlockIndex": 0}}
    yield {"messageStop": {"stopReason": stop_reason}}
    yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 20}}}


async def _events(chunks: list[str], **kw) -> list[dict]:
    return [
        event
        async for event in anthropic_sse_events_from_converse_stream(
            _stream(chunks, **kw), model="moonshotai.kimi-k2.5",
            estimated_input_tokens=10,
        )
    ]


CALL = (
    '<|tool_call_begin|> functions.world_move:0 <|tool_call_argument_begin|> '
    '{"destination_location_id": "Kasukabe Bakery"} <|tool_call_end|>'
)


def _tool_uses(events: list[dict]) -> list[dict]:
    return [
        e["content_block"] for e in events
        if e["type"] == "content_block_start"
        and e["content_block"]["type"] == "tool_use"
    ]


@pytest.mark.asyncio
async def test_a_call_written_as_text_becomes_a_real_one() -> None:
    """The finding itself."""
    events = await _events([CALL])
    blocks = _tool_uses(events)
    assert blocks, (
        "INFRA-07: the model asked to move and the stream carried only "
        "text, so herald did nothing and the turn was billed for nothing"
    )
    assert blocks[0]["name"] == "world_move"
    assert blocks[0]["id"].startswith("toolu_")


@pytest.mark.asyncio
async def test_the_arguments_survive() -> None:
    events = await _events([CALL])
    deltas = [
        e for e in events
        if e["type"] == "content_block_delta"
        and e["delta"]["type"] == "input_json_delta"
    ]
    assert deltas, "the call arrived with no arguments at all"
    assert json.loads(deltas[0]["delta"]["partial_json"]) == {
        "destination_location_id": "Kasukabe Bakery"
    }


@pytest.mark.asyncio
async def test_markers_split_across_deltas_are_still_recognised() -> None:
    """The reason this needs a buffer: run 23 shows the marker and its
    arguments arriving in different chunks, so a parser that examined one
    delta at a time would see nothing at all."""
    chunks = [CALL[:20], CALL[20:55], CALL[55:]]
    assert len(_tool_uses(await _events(chunks))) == 1


@pytest.mark.asyncio
async def test_the_turn_says_it_made_a_call() -> None:
    """herald routes on `stop_reason`. Bedrock says "end_turn" because as
    far as IT is concerned the model only wrote prose."""
    events = await _events([CALL])
    stops = [e for e in events if e["type"] == "message_delta" and e["delta"].get("stop_reason")]
    assert stops[0]["delta"]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_speech_around_a_call_is_not_swallowed() -> None:
    """A resident's actual words must survive a tool call in the same
    block - losing those would be a new bug of the same shape."""
    events = await _events(["I should go. " + CALL + " On my way."])
    said = "".join(
        e["delta"]["text"] for e in events
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "text_delta"
    )
    assert "I should go." in said
    assert "On my way." in said
    assert "<|tool_call" not in said, "the markup was passed through as speech"


@pytest.mark.asyncio
async def test_ordinary_text_is_untouched() -> None:
    events = await _events(["Just a thought, nothing more."])
    said = "".join(
        e["delta"]["text"] for e in events
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "text_delta"
    )
    assert said == "Just a thought, nothing more."
    assert not _tool_uses(events)


@pytest.mark.asyncio
async def test_a_real_tool_use_block_is_left_alone() -> None:
    """The normal path must not change. Nearly every call takes it."""

    async def native():
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {
            "toolUse": {"toolUseId": "toolu_real", "name": "world_say"}}}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {
            "toolUse": {"input": '{"content": "hello"}'}}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}

    events = [
        e async for e in anthropic_sse_events_from_converse_stream(
            native(), model="m", estimated_input_tokens=1)
    ]
    blocks = _tool_uses(events)
    assert len(blocks) == 1 and blocks[0]["id"] == "toolu_real"


# ── the refusals ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arguments_that_are_not_json_stay_as_text() -> None:
    """A turn that did nothing is bad; a turn that did SOMETHING ELSE is
    worse. Never guess at a malformed call."""
    broken = (
        '<|tool_call_begin|> functions.world_move:0 '
        '<|tool_call_argument_begin|> not json at all <|tool_call_end|>'
    )
    events = await _events([broken])
    assert not _tool_uses(events)
    said = "".join(
        e["delta"]["text"] for e in events
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "text_delta"
    )
    assert "not json at all" in said, "the text was deleted rather than returned"


@pytest.mark.asyncio
async def test_an_unterminated_marker_is_returned_not_swallowed() -> None:
    """Run 23 has bare `<|tool_call_begin|>` with nothing after it. Those
    are the resident's own output and must not vanish."""
    events = await _events(["thinking... <|tool_call_begin|>\n<|tool_call_begin|>"])
    said = "".join(
        e["delta"]["text"] for e in events
        if e["type"] == "content_block_delta" and e["delta"]["type"] == "text_delta"
    )
    assert "thinking..." in said
    assert not _tool_uses(events)


def test_the_buffer_gives_up_rather_than_growing_without_limit() -> None:
    """A stream that opens a marker and never closes it must not hold the
    rest of the call in memory."""
    buffer = MarkupBuffer(limit_chars=200)
    buffer.feed("<|tool_call_begin|> functions.x:0 ")
    passthrough, calls = buffer.feed("y" * 500)
    assert calls == []
    assert "y" * 100 in passthrough, "the held text was dropped instead of returned"
    assert buffer.overflowed


def test_both_marker_spellings_are_recognised() -> None:
    """`tool_call_*` and `function_call_*` both appear in run 23's logs.
    Recognising only one would leave the other unparsed for exactly the
    reason this finding exists."""
    buffer = MarkupBuffer()
    _, calls = buffer.feed(
        '<|function_call_begin|> functions.world_eat:0 '
        '<|tool_call_argument_begin|> {"item": "bread"} <|function_call_end|>'
    )
    assert [c.name for c in calls] == ["world_eat"]


# ── ENG-08: the same defect, found twice, closed once ────────────────


class TestTheOpeningMarkerIsOptional:
    """`ENG-08`. INFRA-07's parser keys on `<|tool_call_begin|>`.

    ENG-08 is the same symptom found by a second reviewer, and its
    evidence from run 23 has no such marker - Kimi drops it and writes
    the sanitised name bare:

        world_say:0 <|tool_call_argument_begin|> {"content": "…",
        "listener_agent_id": "n07"} <|tool_call_end|>
        <|tool_calls_section_end|>

    So every turn ENG-08 quotes was STILL lost after INFRA-07 shipped.
    Verified before this fix: all three of the report's spans parsed to
    nothing, which is exactly the state INFRA-07 was written to end.

    The name is then whatever token sits immediately before the argument
    marker - it is the only thing it can be - and everything INFRA-07
    already guarantees still holds: `_NAME` has to match it, arguments
    that are not JSON stay as text, and prose is never mistaken for a
    call. Those three are re-asserted here against the new shape rather
    than assumed, because a looser opening rule is exactly where a
    parser starts inventing calls.
    """

    def test_the_reports_own_world_say_span_parses(self) -> None:
        text = (
            "world_say:0 <|tool_call_argument_begin|> "
            '{"content": "the flour is ready", "listener_agent_id": "n07"} '
            "<|tool_call_end|> <|tool_calls_section_end|>"
        )
        _through, calls = MarkupBuffer().feed(text)
        assert [c.name for c in calls] == ["world_say"]
        assert calls[0].input()["listener_agent_id"] == "n07"

    def test_the_reports_own_world_move_span_parses(self) -> None:
        text = (
            "world_move:9 <|tool_call_argument_begin|> "
            '{"destination_location_id": "business:b_mill"} <|tool_call_end|>'
        )
        _through, calls = MarkupBuffer().feed(text)
        assert [c.name for c in calls] == ["world_move"]
        assert calls[0].input()["destination_location_id"] == "business:b_mill"

    def test_speech_before_a_bare_call_survives(self) -> None:
        """The resident's own words must not be swallowed by the repair."""
        through, calls = MarkupBuffer().feed(
            "I should go to the mill. world_move:0 <|tool_call_argument_begin|> "
            '{"destination_location_id": "mill"} <|tool_call_end|>'
        )
        assert "I should go to the mill." in through
        assert [c.name for c in calls] == ["world_move"]

    def test_a_bare_span_split_across_deltas_still_parses(self) -> None:
        """The reason this is a buffer at all: run 23 shows the markers
        arriving in different chunks, and the NAME can split too.

        The section marker leads, as it does in the corpus - every span
        ENG-08 quotes ends with `<|tool_calls_section_end|>`, so a
        section was opened before it. That is also what arms the
        trailing-token hold: see `MarkupBuffer.saw_markup` for why it is
        not armed on every stream.
        """
        buffer = MarkupBuffer()
        found = []
        for chunk in ["<|tool_calls_section_begin|> ", "world_",
                      "say:0 <|tool_call_", "argument_begin|> ",
                      '{"content": "hi"}', " <|tool_call_end|>"]:
            _through, calls = buffer.feed(chunk)
            found.extend(calls)
        assert [c.name for c in found] == ["world_say"]

    def test_an_ordinary_stream_is_not_buffered_a_word_behind(self) -> None:
        """The cost of the trailing-token hold, and why it is armed
        rather than always on.

        Holding a name-shaped token on EVERY delta defers the last word
        of every ordinary answer to `flush`. Nothing is lost - translate
        emits `flush()` before the stop - but it changes the shape of
        every text stream in the broker, which is a large price for a
        narrow repair. So it starts only once this stream has shown
        markup.
        """
        buffer = MarkupBuffer()
        through, calls = buffer.feed("the bread is ready")
        assert calls == []
        assert through == "the bread is ready", (
            "an ordinary answer was held back a word with no markup in sight"
        )
        assert buffer.flush() == ""

    def test_the_residue_is_named_rather_than_hidden(self) -> None:
        """What this does NOT repair, asserted so it stays known.

        A bare name that splits mid-token AND is the first markup
        anything in the stream has seen is still released as speech
        before the argument marker arrives. No span ENG-08 quotes is
        that case; if one ever is, this test is where the fix goes.
        """
        buffer = MarkupBuffer()
        found = []
        for chunk in ["world_", "say:0 <|tool_call_argument_begin|> ",
                      '{"content": "hi"} <|tool_call_end|>']:
            _through, calls = buffer.feed(chunk)
            found.extend(calls)
        assert found == [], (
            "this now works - good; update the docstring and delete this test"
        )

    def test_a_truncated_name_is_never_called_under_a_shorter_one(self) -> None:
        """The residue must stay a no-op, not become a wrong call.

        The first draft of the recovery took whatever token touched the
        front of the buffer, so "world_" released and "say:0 <|...|>"
        left behind called a tool named `say` when the model had asked
        for `world_say`. A turn that did nothing is bad; a turn that did
        SOMETHING ELSE is worse - this module's own rule, and the reason
        the recovery refuses a name whose front may already be gone.
        """
        buffer = MarkupBuffer()
        names = []
        for chunk in ["world_", "say:0 <|tool_call_argument_begin|> ",
                      '{"content": "hi"} <|tool_call_end|>']:
            _through, calls = buffer.feed(chunk)
            names += [c.name for c in calls]
        assert "say" not in names, "a truncated name was called as a real tool"

    def test_plain_prose_is_not_mistaken_for_a_call(self) -> None:
        """The failure mode that matters more than the one being fixed:
        a turn that did nothing is bad, a turn that did SOMETHING ELSE is
        worse. A looser opening rule is where that starts.

        Asserted on passthrough PLUS flush, which is what actually
        reaches the consumer: the buffer now holds a trailing
        name-shaped token on the chance the argument marker follows in
        the next delta, and `translate.py` emits `flush()` as a text
        delta at `messageStop`. One word of latency, nothing dropped.
        """
        for prose in (
            "I think the bread is ready.",
            "She said: functions are hard.",
            "tool_call: I should probably move.",
            "The mill is closed. I will try tomorrow.",
        ):
            buffer = MarkupBuffer()
            through, calls = buffer.feed(prose)
            assert calls == [], prose
            assert through + buffer.flush() == prose, prose

    def test_the_held_word_is_not_lost_at_the_end_of_a_stream(self) -> None:
        """The cost of holding a trailing token is one delta of latency;
        it must never be a dropped word. `flush` is emitted before
        `message_delta` in translate.py for exactly this."""
        buffer = MarkupBuffer()
        through, _calls = buffer.feed("the bread is ready")
        assert through + buffer.flush() == "the bread is ready"

    def test_a_bare_span_with_bad_json_stays_as_text(self) -> None:
        text = "world_say:0 <|tool_call_argument_begin|> not json <|tool_call_end|>"
        buffer = MarkupBuffer()
        through, calls = buffer.feed(text)
        assert calls == []
        assert "world_say" in through + buffer.flush()

    def test_a_name_that_is_not_a_name_is_not_taken(self) -> None:
        """Whatever precedes the argument marker still has to LOOK like a
        tool name, or a stray sentence ending in punctuation becomes one."""
        through, calls = MarkupBuffer().feed(
            '...ready! <|tool_call_argument_begin|> {"a": 1} <|tool_call_end|>'
        )
        assert calls == []
