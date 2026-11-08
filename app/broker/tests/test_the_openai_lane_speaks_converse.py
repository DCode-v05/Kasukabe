"""An OpenAI model arrives through the same door as Kimi, or not at all.

`broker/service.py` hands a prepared request to `lane.gateway` and every
consumer downstream - the Anthropic translation the engine reads, the token
accounting, the pricing, the trace - reads the Bedrock Converse shape that
comes back. So the OpenAI gateway's whole contract is: take Converse, return
Converse. If it gets that right, nothing else in the broker needs to know
OpenAI exists.

These tests never touch the network. `httpx.MockTransport` answers with real
OpenAI response bodies, so what is under test is the translation and not
OpenAI's uptime.

WHY THE LANE EXISTS AT ALL, measured rather than assumed: on this project's
real traffic to date - 458.5M input and 6.0M output tokens across 32,739
calls - the same work costs $175.94 on Kimi K2.5 and $48.87 on `gpt-6-luna`.
This town spends 98.7% of its money on input tokens, and Luna prices those at
$0.10/M against Kimi's $0.36.
"""
from __future__ import annotations

import json

import httpx
import pytest

from broker.config import BEDROCK_MODELS, MODEL_ALLOWLIST, OPENAI_MODELS
from broker.errors import UpstreamError, UpstreamOverloadedError
from broker.openai_gateway import (
    OpenAIGateway,
    converse_request_to_openai,
    openai_response_to_converse,
)

CONVERSE = {
    "modelId": "gpt-6-luna",
    "system": [{"text": "You are Delia Okonjo, the town's pharmacist."}],
    "messages": [
        {"role": "user", "content": [{"text": "It is game-day 2, 06:10."}]},
        {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "world_vote", "input": {"candidate_agent_id": "n16"}}}
        ]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "t1", "content": [{"text": "vote recorded"}], "status": "success"}}
        ]},
    ],
    "inferenceConfig": {"maxTokens": 768},
    "toolConfig": {"tools": [
        {"toolSpec": {"name": "world_vote", "description": "Cast your vote.",
                      "inputSchema": {"json": {"type": "object"}}}}
    ]},
}


def _gateway(handler) -> OpenAIGateway:
    return OpenAIGateway(
        "sk-test", "gpt-6-luna",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_the_system_prompt_becomes_a_system_message() -> None:
    body = converse_request_to_openai(CONVERSE, "gpt-6-luna")

    assert body["messages"][0] == {
        "role": "system", "content": "You are Delia Okonjo, the town's pharmacist."
    }


def test_a_tool_call_and_its_result_become_two_messages() -> None:
    """Converse carries a toolUse and its toolResult as content blocks on
    two turns; OpenAI needs an assistant message with `tool_calls` and a
    separate message with role `tool`. Getting this wrong is what makes a
    continuation turn 400."""
    body = converse_request_to_openai(CONVERSE, "gpt-6-luna")
    roles = [m["role"] for m in body["messages"]]

    assert roles == ["system", "user", "assistant", "tool"]
    call = body["messages"][2]["tool_calls"][0]
    assert call["function"]["name"] == "world_vote"
    assert json.loads(call["function"]["arguments"]) == {"candidate_agent_id": "n16"}
    assert body["messages"][3]["tool_call_id"] == "t1"


def test_the_lane_model_wins_over_the_request() -> None:
    """`modelId` is whatever the caller asked for; routing has already
    decided this lane serves it. Sending Bedrock's spelling to OpenAI earns
    a 404."""
    body = converse_request_to_openai({**CONVERSE, "modelId": "moonshotai.kimi-k2.5"}, "gpt-6-luna")

    assert body["model"] == "gpt-6-luna"


def test_the_token_cap_uses_the_name_the_new_models_accept() -> None:
    """`max_tokens` is rejected outright by the newer models rather than
    ignored, so a cap sent under the old name is a failed call."""
    body = converse_request_to_openai(CONVERSE, "gpt-6-luna")

    assert body["max_completion_tokens"] == 768
    assert "max_tokens" not in body


def test_tools_arrive_with_reasoning_switched_off() -> None:
    """Found by the first live call, which the recorded-body tests could not
    have caught. The API answered:

        "Function tools with reasoning_effort are not supported for
         gpt-6-luna in /v1/chat/completions. To use function tools, use
         /v1/responses or set reasoning_effort to 'none'."

    Every action a resident takes is a tool call, so without this the lane
    serves nothing at all - a 400 on the very first turn of a paid run.

    It is also the cheaper side of the trade: reasoning tokens bill as
    output, and output is $0.50/M against input's $0.10/M in a town that
    spends 98.7% of its money on input.
    """
    body = converse_request_to_openai(CONVERSE, "gpt-6-luna")

    assert body["reasoning_effort"] == "none"


def test_a_toolless_call_is_left_alone() -> None:
    """Only the tools case is refused by the API, so only the tools case is
    touched. A summariser asking for prose keeps the model's own default."""
    no_tools = {k: v for k, v in CONVERSE.items() if k != "toolConfig"}

    assert "reasoning_effort" not in converse_request_to_openai(no_tools, "gpt-6-luna")


def test_tools_keep_their_schema() -> None:
    body = converse_request_to_openai(CONVERSE, "gpt-6-luna")
    fn = body["tools"][0]["function"]

    assert fn["name"] == "world_vote"
    assert fn["parameters"] == {"type": "object"}


def test_text_and_tool_calls_come_back_as_converse_blocks() -> None:
    converse = openai_response_to_converse({
        "choices": [{"message": {"content": "I will vote.", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "world_vote", "arguments": '{"candidate_agent_id":"n16"}'}}
        ]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 48},
    })

    blocks = converse["output"]["message"]["content"]
    assert blocks[0] == {"text": "I will vote."}
    assert blocks[1]["toolUse"]["name"] == "world_vote"
    assert blocks[1]["toolUse"]["input"] == {"candidate_agent_id": "n16"}
    assert converse["stopReason"] == "tool_use"
    assert converse["usage"] == {"inputTokens": 1200, "outputTokens": 48, "totalTokens": 0}


def test_a_content_filter_stays_a_refusal() -> None:
    """`content_filter` must not flatten to end_turn. A turn stopped by a
    filter and a turn that chose to stop are different events, and
    `a_turn_that_tried_to_act` exists to keep them apart."""
    converse = openai_response_to_converse(
        {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}
    )

    assert converse["stopReason"] == "content_filtered"


def test_unparsable_tool_arguments_are_carried_not_swallowed() -> None:
    """An empty dict would look like a call the model made with no
    arguments. The raw text is kept where a reader can see it."""
    converse = openai_response_to_converse({
        "choices": [{"message": {"tool_calls": [
            {"id": "c1", "function": {"name": "world_vote", "arguments": "{oh dear"}}
        ]}, "finish_reason": "tool_calls"}],
    })

    assert converse["output"]["message"]["content"][0]["toolUse"]["input"] == {
        "_unparsed_arguments": "{oh dear"
    }


@pytest.mark.asyncio
async def test_a_whole_call_round_trips() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Good morning."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 12},
        })

    got = await _gateway(handler).converse(CONVERSE)

    assert got["output"]["message"]["content"] == [{"text": "Good morning."}]
    assert got["stopReason"] == "end_turn"
    assert got["usage"]["inputTokens"] == 900


@pytest.mark.asyncio
async def test_a_rate_limit_is_overload_so_the_pool_re_routes() -> None:
    gw = _gateway(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}))

    with pytest.raises(UpstreamOverloadedError):
        await gw.converse(CONVERSE)


@pytest.mark.asyncio
async def test_a_bad_request_is_not_retried() -> None:
    """A 400 is the model answering. Asking again gets the same answer and
    spends the money twice."""
    gw = _gateway(lambda r: httpx.Response(400, json={"error": {"message": "bad tool schema"}}))

    with pytest.raises(UpstreamError) as caught:
        await gw.converse(CONVERSE)
    assert not isinstance(caught.value, UpstreamOverloadedError)


def _sse(*chunks: dict) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_a_stream_becomes_converse_events_in_order() -> None:
    """Converse numbers its blocks and brackets each with a start and a
    stop; OpenAI streams a flat sequence. The mapping is the only hard part
    of this gateway."""
    body = _sse(
        {"choices": [{"delta": {"content": "Morning"}}]},
        {"choices": [{"delta": {"content": ", Hester."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "world_vote", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"candidate_agent_id":"n16"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 1500, "completion_tokens": 40}},
    )
    gw = _gateway(lambda r: httpx.Response(200, text=body,
                                           headers={"content-type": "text/event-stream"}))

    events = [e async for e in gw.converse_stream(CONVERSE)]
    kinds = [next(iter(e)) for e in events]

    assert kinds[0] == "contentBlockDelta"
    assert "contentBlockStart" in kinds
    assert kinds[-2] == "messageStop"
    assert kinds[-1] == "metadata", "usage must be last; tokens.py reads it off the end"

    text = "".join(
        e["contentBlockDelta"]["delta"]["text"]
        for e in events
        if "contentBlockDelta" in e and "text" in e["contentBlockDelta"]["delta"]
    )
    assert text == "Morning, Hester."
    assert events[-1]["metadata"]["usage"] == {"inputTokens": 1500, "outputTokens": 40}
    assert events[-2]["messageStop"]["stopReason"] == "tool_use"


@pytest.mark.asyncio
async def test_text_and_a_tool_call_get_different_block_indexes() -> None:
    """Two blocks sharing an index would splice a tool call into the
    narration, which is exactly the corruption TRADE-01 cost 6,424 calls."""
    body = _sse(
        {"choices": [{"delta": {"content": "hm"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "world_rest", "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )
    gw = _gateway(lambda r: httpx.Response(200, text=body,
                                           headers={"content-type": "text/event-stream"}))

    events = [e async for e in gw.converse_stream(CONVERSE)]
    text_ix = next(e["contentBlockDelta"]["contentBlockIndex"] for e in events
                   if "contentBlockDelta" in e and "text" in e["contentBlockDelta"]["delta"])
    tool_ix = next(e["contentBlockStart"]["contentBlockIndex"] for e in events
                   if "contentBlockStart" in e)

    assert text_ix != tool_ix
    stops = [e["contentBlockStop"]["contentBlockIndex"] for e in events if "contentBlockStop" in e]
    assert sorted(stops) == sorted({text_ix, tool_ix}), "every opened block is closed"


def test_openai_models_are_not_expected_to_have_bedrock_lanes() -> None:
    """Regions, inference profiles and IAM grants are Bedrock's three, and
    an OpenAI model has none of them. Anything reasoning about those must
    use BEDROCK_MODELS, or it asks for an ARN that cannot exist."""
    assert set(BEDROCK_MODELS).isdisjoint(OPENAI_MODELS)
    assert set(BEDROCK_MODELS) | set(OPENAI_MODELS) == set(MODEL_ALLOWLIST)
