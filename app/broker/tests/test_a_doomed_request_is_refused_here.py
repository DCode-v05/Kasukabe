"""A prompt that cannot fit must not be sent, and must be named when it is not.

THE INCIDENT, measured in data/loop4.db:

    Bedrock ValidationException: This model's maximum context length is
    163840 tokens. However, you requested 4096 output tokens and your
    prompt contains at least 159745 input tokens, for a total of at least
    163841 tokens.

Over by exactly one token. Eighteen of the run's nineteen failures were
this, all from one resident, and every one of them was a `wm_flush` - the
engine's working-memory flush, which packs every buffered tool_call and
tool_result into a single user message with no tools, no system prompt and
no bound. 41 of 1,047 calls, median 1,221 tokens, worst 99,591 among the
ones that succeeded.

TWO SEPARATE FAULTS, and this file is about the broker's half of each.

1. The flush was classified COGNITION and served on a Kimi lane an agent
   needed, and billed to that agent's wallet, because it carries no marker
   any classifier recognised. It is memory machinery. `wm_flush` is a
   verbatim string in the request the frozen memory layer builds, so it is
   as reliable a marker as the summariser's own instruction text.

2. The broker paid a full Bedrock round trip to be told a request could
   not fit, and then reported it as an upstream error - which reads, in
   every report, as "Bedrock was unhappy" rather than "we sent something
   impossible". The arithmetic is available before dispatch.

WHAT THIS DOES NOT DO. It does not trim the prompt. Silently dropping
messages so a doomed call squeaks through would produce an answer computed
from a conversation nobody chose, which is worse than no answer: the run
would look healthy and the record would be fiction. When only max_tokens
is in the way it is clamped, loudly and in the trace, because a shorter
reply IS the same conversation. When the prompt alone will not fit,
nothing can save it and the refusal says so with the numbers.

THE UNBOUNDED ACCUMULATION ITSELF is in frozen memory/ under ROADMAP Law 1
and is recorded as a finding rather than patched around here.
"""

from __future__ import annotations

import pytest

from broker.config import DEEPSEEK_MODEL_ID, KIMI_MODEL_ID
from broker.errors import InvalidRequestError
from broker.fit import (
    CONTEXT_WINDOW_TOKENS,
    MIN_USABLE_OUTPUT_TOKENS,
    fit_to_context_window,
)
from broker.lanes import CallClass, classify_purpose
from broker.purpose import infer_purpose


def flush_body(entries: int = 1, max_tokens: int = 4096) -> dict:
    """A working-memory flush, shaped like the real ones: one user message
    holding a JSON array of buffered tool calls, no system, no tools."""
    blob = ", ".join(
        '{"type": "tool_call", "source": "wm_flush", "subject": "", '
        '"content": "{\\"tool\\": \\"world:look_around\\"}"}'
        for _ in range(entries)
    )
    return {
        "model": KIMI_MODEL_ID,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [{"type": "text", "text": f"[{blob}]"}]}],
    }


def test_a_working_memory_flush_is_background() -> None:
    """Every context-overflow failure in loop4 was one of these, served on
    a Kimi lane and billed to n09's own wallet."""
    purpose = infer_purpose("", flush_body())
    assert classify_purpose(purpose) is CallClass.BACKGROUND
    assert purpose.startswith("auto:"), "an inferred tag must say it was inferred"


def test_an_agent_turn_that_merely_mentions_the_flush_is_not_rerouted() -> None:
    """The same veto every other marker rule gets: a resident's turn
    carries the town's tool schemas and a flush never does."""
    body = flush_body()
    body["tools"] = [{"name": "world_say"}]
    assert classify_purpose(infer_purpose("", body)) is CallClass.COGNITION


def test_a_request_that_fits_is_left_completely_alone() -> None:
    body = flush_body(entries=2, max_tokens=4096)
    fitted, note = fit_to_context_window(body, DEEPSEEK_MODEL_ID)
    assert fitted is body, "a healthy request must not even be copied"
    assert note is None


def test_max_tokens_is_clamped_when_that_alone_makes_it_fit() -> None:
    """The exact loop4 case: over by one token. A shorter reply is the
    same conversation, so this is a clamp rather than a refusal."""
    window = CONTEXT_WINDOW_TOKENS[DEEPSEEK_MODEL_ID]
    body = {
        "model": KIMI_MODEL_ID,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "x" * ((window - 4095) * 4)}],
    }
    fitted, note = fit_to_context_window(body, DEEPSEEK_MODEL_ID)
    assert fitted["max_tokens"] < 4096
    assert note and "max_tokens" in note
    assert fitted is not body, "the caller's dict must not be mutated"


def test_a_clamp_never_goes_below_a_usable_answer() -> None:
    """A reply of forty tokens is not a cheaper answer, it is a truncated
    one that costs the same to produce. Below the floor it is a refusal."""
    window = CONTEXT_WINDOW_TOKENS[DEEPSEEK_MODEL_ID]
    body = {
        "model": KIMI_MODEL_ID,
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": "x" * ((window - MIN_USABLE_OUTPUT_TOKENS // 2) * 4)}
        ],
    }
    with pytest.raises(InvalidRequestError):
        fit_to_context_window(body, DEEPSEEK_MODEL_ID)


def test_a_prompt_bigger_than_the_window_is_refused_with_the_numbers() -> None:
    """Nothing can save it, and the refusal has to say why or the report
    reads "Bedrock was unhappy" instead of "we sent something impossible"."""
    window = CONTEXT_WINDOW_TOKENS[DEEPSEEK_MODEL_ID]
    body = {
        "model": KIMI_MODEL_ID,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "x" * (window * 8)}],
    }
    with pytest.raises(InvalidRequestError) as caught:
        fit_to_context_window(body, DEEPSEEK_MODEL_ID)
    message = str(caught.value)
    assert f"{window:,}" in message
    assert "wm_flush" in message or "prompt" in message


def test_the_prompt_is_never_trimmed_to_make_it_fit() -> None:
    """The one thing that must not happen. An answer computed from a
    conversation nobody chose is worse than no answer: the run looks
    healthy and the record is fiction."""
    window = CONTEXT_WINDOW_TOKENS[DEEPSEEK_MODEL_ID]
    body = {
        "model": KIMI_MODEL_ID,
        "max_tokens": 8192,
        "messages": [
            {"role": "user", "content": "a" * 400},
            {"role": "assistant", "content": "b" * 400},
            {"role": "user", "content": "c" * ((window - 6000) * 4)},
        ],
    }
    fitted, note = fit_to_context_window(body, DEEPSEEK_MODEL_ID)
    assert note is not None, "this request was supposed to need a clamp"
    assert len(fitted["messages"]) == 3
    assert fitted["messages"][2]["content"] == body["messages"][2]["content"]


def test_both_lanes_have_a_window_and_they_are_not_the_same() -> None:
    """Cognition overflows from Kimi onto DeepSeek when the Kimi lanes
    saturate, so the SERVING model decides, not the requested one. A table
    missing an entry would silently skip the check for that lane."""
    assert set(CONTEXT_WINDOW_TOKENS) >= {KIMI_MODEL_ID, DEEPSEEK_MODEL_ID}
    assert all(window > 100_000 for window in CONTEXT_WINDOW_TOKENS.values())


def test_an_unknown_model_is_left_alone_rather_than_guessed_at() -> None:
    """A new lane must not be silently checked against somebody else's
    window. No entry means no opinion."""
    body = flush_body()
    fitted, note = fit_to_context_window(body, "some.model.we.have.not.met")
    assert fitted is body and note is None


@pytest.mark.asyncio
async def test_prepare_call_refuses_a_doomed_request_before_any_dispatch() -> None:
    """Gate 1 of Build Spec v2 section 1. A module the dispatch path never
    reaches passes its own unit tests and is still broken - which is
    exactly what an unwired context check would be, because the symptom it
    fixes only ever appears against a real lane.

    Severing the wire fails this: remove the fit_to_context_window call
    from prepare_call and the request sails through to a Bedrock the mock
    will happily answer.
    """
    from broker.tests.test_service import build_service

    service, ledger, _bedrock, _trace, _events = build_service()
    ledger.seed("agent-1", balance_cents=100_000)

    smallest = min(CONTEXT_WINDOW_TOKENS.values())
    body = {
        "model": KIMI_MODEL_ID,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "x" * (smallest * 8)}],
    }
    with pytest.raises(InvalidRequestError) as caught:
        await service.prepare_call(body, {"x-api-key": "agent-1"})
    assert "Nothing was sent" in str(caught.value)


@pytest.mark.asyncio
async def test_a_request_that_only_needs_a_clamp_still_goes_through() -> None:
    """The other half, and the one that would be easy to get wrong: a
    clamp must not become a refusal. A run in which every large prompt is
    refused is a quieter failure than the one this replaced."""
    from broker.tests.test_service import build_service

    service, ledger, _bedrock, _trace, _events = build_service()
    ledger.seed("agent-1", balance_cents=1_000_000)

    smallest = min(CONTEXT_WINDOW_TOKENS.values())
    body = {
        "model": KIMI_MODEL_ID,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": "x" * ((smallest - 6000) * 4)}],
    }
    prepared = await service.prepare_call(body, {"x-api-key": "agent-1"})
    assert prepared is not None
