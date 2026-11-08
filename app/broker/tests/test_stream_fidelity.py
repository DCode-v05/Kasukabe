"""Streaming fidelity: nothing the model produced may be lost on the way in.

Build Spec v2 sections 4.2 and 4.8. This file is the contract test for
defect D1 (938 successful calls recorded with zero content, 360,250 output
tokens billed against them) and defect D2 (reasoning never carried on the
streaming path).

WHY THE EXISTING SUITE MISSED IT. Every streaming fixture in
test_translate.py opens with an explicit ``contentBlockStart``. Real
Bedrock sends that event for ``toolUse`` blocks and frequently does NOT
send it for plain text: a text block arrives as bare ``contentBlockDelta``.
So the suite tested a shape the upstream does not always produce, and the
one shape it does produce was the broken one. Every fixture below that
omits ``contentBlockStart`` is deliberate, and is the point.

The two invariants this file defends:

  1. The SSE stream broker emits is Anthropic-shaped, and the Anthropic
     wire GUARANTEES a ``content_block_start`` before any delta for that
     index. A consumer is entitled to key off it. Both of broker's own
     consumers do (StreamAccumulator here, herald's stream parser in the
     engine), so emitting a delta for an index that never started is a
     contract violation regardless of what any one consumer tolerates.

  2. Tokens billed and content recorded cannot disagree. If Bedrock
     reports output tokens, something was generated, and a record showing
     none is a lie about a call we paid for.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from broker.service import PreparedCall, StreamAccumulator, reconcile_usage_and_content
from broker.tests.test_service import build_service
from broker.translate import anthropic_sse_events_from_converse_stream


async def bedrock_events(raw: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for event in raw:
        yield event


async def translate(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        async for event in anthropic_sse_events_from_converse_stream(
            bedrock_events(raw),
            model="moonshotai.kimi-k2.5",
            estimated_input_tokens=100,
            on_usage=None,
        )
    ]


async def assemble(raw: list[dict[str, Any]]) -> StreamAccumulator:
    accumulator = StreamAccumulator()
    for event in await translate(raw):
        accumulator.ingest(event)
    return accumulator


def text_of(accumulator: StreamAccumulator) -> str:
    return "".join(
        block.get("text", "")
        for block in accumulator.content()
        if block.get("type") == "text"
    )


# A text response exactly as Bedrock actually sends one: no contentBlockStart.
BARE_TEXT = [
    {"messageStart": {"role": "assistant"}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Morning, Wren. "}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Have you the flour?"}}},
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {"messageStop": {"stopReason": "end_turn"}},
    {"metadata": {"usage": {"inputTokens": 900, "outputTokens": 14, "totalTokens": 914}, "metrics": {}}},
]


# ── 4.2 Streaming assembly under adversarial event orders ────────────


@pytest.mark.asyncio
async def test_text_arriving_with_no_content_block_start_is_still_assembled() -> None:
    """D1, exactly. This is the single most important test in the suite.

    Before the fix this assembles to "" and the trace records
    {"content": [], "stop_reason": "end_turn"} - the precise shape found
    938 times in dryrun.db.
    """
    accumulator = await assemble(BARE_TEXT)
    assert text_of(accumulator) == "Morning, Wren. Have you the flour?"
    assert accumulator.content(), "content was dropped entirely"


@pytest.mark.asyncio
async def test_translator_emits_a_start_before_any_delta_for_that_index() -> None:
    """The wire contract, not one consumer's tolerance.

    Asserted over the event sequence itself so a future consumer that
    also keys off content_block_start cannot be broken by this path
    again.
    """
    events = await translate(BARE_TEXT)
    started: set[int] = set()
    for event in events:
        if event["type"] == "content_block_start":
            started.add(event["index"])
        elif event["type"] in ("content_block_delta", "content_block_stop"):
            assert event["index"] in started, (
                f"{event['type']} for index {event['index']} with no preceding "
                "content_block_start - violates the Anthropic wire contract"
            )


@pytest.mark.asyncio
async def test_a_started_block_is_not_started_twice() -> None:
    """The synthetic start must not double-fire once a real one arrived."""
    events = await translate(
        [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "a"}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "b"}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ]
    )
    starts = [e for e in events if e["type"] == "content_block_start" and e["index"] == 0]
    assert len(starts) == 1
    accumulator = StreamAccumulator()
    for event in events:
        accumulator.ingest(event)
    assert text_of(accumulator) == "ab"


@pytest.mark.asyncio
async def test_interleaved_text_and_tool_use_assemble_independently() -> None:
    """A turn that says something AND calls a tool - the shape every
    real conversation turn takes. Text at index 0 with no start, tool at
    index 1 with one."""
    accumulator = await assemble(
        [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "I'll fetch it."}}},
            {
                "contentBlockStart": {
                    "contentBlockIndex": 1,
                    "start": {"toolUse": {"toolUseId": "t1", "name": "world_say"}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 1,
                    "delta": {"toolUse": {"input": '{"content":"on my way"}'}},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 1}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "tool_use"}},
        ]
    )
    blocks = accumulator.content()
    assert [b["type"] for b in blocks] == ["text", "tool_use"]
    assert blocks[0]["text"] == "I'll fetch it."
    assert blocks[1]["name"] == "world_say"
    assert blocks[1]["input"] == {"content": "on my way"}


@pytest.mark.asyncio
async def test_block_that_starts_and_never_stops_still_yields_its_text() -> None:
    """A stream cut off before contentBlockStop. Partial text is a
    finding; empty text is a lie."""
    accumulator = await assemble(
        [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "half a sen"}}},
            {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 3}, "metrics": {}}},
        ]
    )
    assert text_of(accumulator) == "half a sen"


@pytest.mark.asyncio
async def test_out_of_order_indices_preserve_arrival_order() -> None:
    accumulator = await assemble(
        [
            {"contentBlockDelta": {"contentBlockIndex": 2, "delta": {"text": "second"}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "first"}}},
            {"contentBlockStop": {"contentBlockIndex": 2}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ]
    )
    assert [b["text"] for b in accumulator.content()] == ["second", "first"]


@pytest.mark.asyncio
async def test_stop_for_an_index_that_never_started_is_survivable() -> None:
    """Malformed upstream must not take the turn down."""
    accumulator = await assemble(
        [
            {"contentBlockStop": {"contentBlockIndex": 7}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "fine"}}},
            {"messageStop": {"stopReason": "end_turn"}},
        ]
    )
    assert text_of(accumulator) == "fine"
    assert accumulator.stop_reason == "end_turn"


# ── 4.2 / D2 Reasoning on the streaming path ─────────────────────────


@pytest.mark.asyncio
async def test_reasoning_delta_becomes_a_thinking_block() -> None:
    """D2. The streaming translator has no reasoningContent branch at
    all today, so a model that reasons is recorded as one that did not."""
    accumulator = await assemble(
        [
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"text": "She asked about flour. "}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"text": "I have none."}},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ]
    )
    blocks = accumulator.content()
    assert [b["type"] for b in blocks] == ["thinking"]
    assert blocks[0]["thinking"] == "She asked about flour. I have none."


@pytest.mark.asyncio
async def test_reasoning_delta_is_emitted_as_a_thinking_delta_herald_understands() -> None:
    """herald/provider.py yields StreamEvent(type="thinking") only on a
    delta typed thinking_delta. Any other spelling is silently skipped
    there, so the engine's ThinkingDelta - and therefore the session
    log's reasoning field - stays empty."""
    events = await translate(
        [
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"text": "weighing it up"}},
                }
            }
        ]
    )
    deltas = [e for e in events if e["type"] == "content_block_delta"]
    assert deltas, "no delta emitted for reasoning content"
    assert deltas[0]["delta"]["type"] == "thinking_delta"
    assert deltas[0]["delta"]["thinking"] == "weighing it up"

    starts = [e for e in events if e["type"] == "content_block_start"]
    assert starts and starts[0]["content_block"]["type"] == "thinking"


@pytest.mark.asyncio
async def test_reasoning_and_speech_are_recorded_as_separate_blocks() -> None:
    """Spec 3.2: what the model thought and what it said are two fields,
    never merged and never substituted for one another."""
    accumulator = await assemble(
        [
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"text": "he is owed money"}},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "I'll pay you Friday."}}},
            {"contentBlockStop": {"contentBlockIndex": 1}},
        ]
    )
    blocks = accumulator.content()
    assert [b["type"] for b in blocks] == ["thinking", "text"]
    assert blocks[0]["thinking"] == "he is owed money"
    assert blocks[1]["text"] == "I'll pay you Friday."


@pytest.mark.asyncio
async def test_redacted_reasoning_is_distinguishable_from_absent_reasoning() -> None:
    """"Did not think" and "was not allowed to show it" must never look
    the same - the same rule content_block_from_bedrock already follows
    on the non-streaming path."""
    accumulator = await assemble(
        [
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"redactedContent": "AAAA"}},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ]
    )
    blocks = accumulator.content()
    assert [b["type"] for b in blocks] == ["redacted_thinking"]


# ── 4.8 Billed and recorded cannot disagree ──────────────────────────


@pytest.mark.asyncio
async def test_a_stream_billed_for_output_never_records_empty_content() -> None:
    """The invariant D1 violated 938 times. Spec threshold 9.4 is zero."""
    accumulator = await assemble(BARE_TEXT)
    body = {"content": accumulator.content(), "stop_reason": accumulator.stop_reason}
    assert reconcile_usage_and_content(output_tokens=14, body=body) is None


def test_reconciliation_flags_billed_output_with_no_recorded_content() -> None:
    """The standing check spec 7.3 requires. One line, and D1 would have
    been caught on its first run instead of its nine-hundred-and-
    thirty-eighth."""
    problem = reconcile_usage_and_content(
        output_tokens=195, body={"content": [], "stop_reason": "end_turn"}
    )
    assert problem is not None
    assert "195" in problem


def test_reconciliation_accepts_a_genuinely_empty_response() -> None:
    """Zero tokens and zero content agree, and must not be flagged."""
    assert reconcile_usage_and_content(output_tokens=0, body={"content": []}) is None


def test_reconciliation_accepts_a_missing_body() -> None:
    """A failed call before dispatch has no body and owes no content."""
    assert reconcile_usage_and_content(output_tokens=0, body=None) is None


def test_reconciliation_flags_a_body_whose_blocks_are_all_empty() -> None:
    """Blocks present but carrying nothing is the same lie in a longer
    coat: the record shows structure and no words."""
    problem = reconcile_usage_and_content(
        output_tokens=40,
        body={"content": [{"type": "text", "text": ""}], "stop_reason": "end_turn"},
    )
    assert problem is not None


# ── 4.2 The accumulator's own tolerance, tested directly ─────────────


def test_accumulator_creates_a_block_for_an_unstarted_delta() -> None:
    """Belt and braces. The translator is fixed to always emit a start,
    but a consumer that silently drops data when its input is imperfect
    is how this defect survived fifteen runs. It must degrade to
    recording the text, never to recording nothing.
    """
    accumulator = StreamAccumulator()
    accumulator.ingest(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "orphan"}}
    )
    assert text_of(accumulator) == "orphan"


def test_accumulator_counts_deltas_it_saw() -> None:
    """Spec 7.2: delta counts per turn, so an assembly bug shows as a
    mismatch between deltas seen and content recorded rather than as a
    plausible-looking empty result."""
    accumulator = StreamAccumulator()
    for chunk in ("a", "b", "c"):
        accumulator.ingest(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk},
            }
        )
    accumulator.ingest(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "thinking_delta", "thinking": "hm"},
        }
    )
    assert accumulator.delta_counts() == {"text": 3, "thinking": 1, "tool_use": 0}


# ── 1 Gate 1: the check is reached by the production path ────────────


def prepared_call_for(service: Any, agent_id: str = "a0001") -> PreparedCall:
    """A PreparedCall shaped exactly as prepare_call() builds one, so
    settle() is exercised on its real argument type rather than a stub."""
    lane = next(iter(service.lane_pool.lanes_by_model.values()))[0]
    return PreparedCall(
        agent_id=agent_id,
        payer_agent_id=agent_id,
        model=lane.model,
        requested_model=lane.model,
        region=lane.region,
        call_class="cognition",
        lane=lane,
        purpose="unspecified",
        bedrock_request={},
        request_body={"messages": []},
        estimated_input_tokens=10,
        rate_limit_wait_s=0.0,
        start_monotonic=0.0,
    )


@pytest.mark.asyncio
async def test_settle_records_content_loss_when_billed_output_is_unrecorded() -> None:
    """Gate 1 of the definition of done: wired to a live call site.

    reconcile_usage_and_content existing is worth nothing if settle() never
    calls it, which is the shape of every defect in this project's wiring
    audits. Severing the call fails this test.
    """
    service, *_ = build_service()
    await service.settle(
        prepared_call_for(service),
        input_tokens=900,
        output_tokens=195,
        stop_reason="end_turn",
        response_body={"content": [], "stop_reason": "end_turn"},
        error=None,
    )
    assert service.content_loss, "settle() did not run the reconciliation"
    loss = service.content_loss[-1]
    assert loss["agent_id"] == "a0001"
    assert loss["output_tokens"] == 195


@pytest.mark.asyncio
async def test_settle_stays_silent_when_content_and_billing_agree() -> None:
    """The alarm must not cry wolf: a healthy call records nothing."""
    service, *_ = build_service()
    await service.settle(
        prepared_call_for(service),
        input_tokens=900,
        output_tokens=14,
        stop_reason="end_turn",
        response_body={"content": [{"type": "text", "text": "Morning, Wren."}]},
        error=None,
    )
    assert service.content_loss == []
