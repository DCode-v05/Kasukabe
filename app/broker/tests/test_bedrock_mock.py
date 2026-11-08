"""MockBedrockGateway: shape-compatible with real botocore converse/
converse_stream output (see bedrock.py's module docstring - translate.py
must run through the identical code path against either)."""

from __future__ import annotations

import pytest

from broker.bedrock import MockBedrockGateway


@pytest.mark.asyncio
async def test_converse_returns_expected_shape() -> None:
    gateway = MockBedrockGateway(fixed_text="hello there")
    request = {"modelId": "moonshotai.kimi-k2.5", "messages": [{"role": "user", "content": [{"text": "hi"}]}]}

    response = await gateway.converse(request)

    assert response["output"]["message"]["role"] == "assistant"
    assert response["output"]["message"]["content"] == [{"text": "hello there"}]
    assert response["stopReason"] == "end_turn"
    assert response["usage"]["inputTokens"] > 0
    assert response["usage"]["outputTokens"] > 0
    assert gateway.calls == [request]


@pytest.mark.asyncio
async def test_converse_stream_yields_full_anthropic_shaped_event_sequence() -> None:
    """INFRA-23: a TEXT block arrives with no `contentBlockStart`.

    This used to assert the opposite, because the mock always sent one and
    real Bedrock frequently does not. That mismatch is how defect D1
    survived fifteen runs - 938 calls recorded with zero content and
    360,250 output tokens billed against them - with the suite green the
    whole way, because everything end-to-end went through this mock and
    this mock produced a shape the upstream does not.
    """
    gateway = MockBedrockGateway(fixed_text="ab")
    request = {"modelId": "moonshotai.kimi-k2.5", "messages": []}

    events = [event async for event in gateway.converse_stream(request)]

    keys = [next(iter(e)) for e in events]
    assert keys == [
        "messageStart",
        "contentBlockDelta",
        "contentBlockStop",
        "messageStop",
        "metadata",
    ]
    assert events[1]["contentBlockDelta"]["delta"]["text"] == "ab"


@pytest.mark.asyncio
async def test_the_friendly_text_shape_is_available_when_asked_for() -> None:
    """Some upstreams DO send it, so the mock can still produce it - in one
    visible word rather than by accident."""
    gateway = MockBedrockGateway(fixed_text="ab", text_content_block_start=True)

    events = [
        event
        async for event in gateway.converse_stream(
            {"modelId": "moonshotai.kimi-k2.5", "messages": []}
        )
    ]

    assert [next(iter(e)) for e in events] == [
        "messageStart",
        "contentBlockStart",
        "contentBlockDelta",
        "contentBlockStop",
        "messageStop",
        "metadata",
    ]


@pytest.mark.asyncio
async def test_a_tool_use_block_still_gets_its_start() -> None:
    """The half that must NOT change: real Bedrock does send
    `contentBlockStart` for toolUse, and herald keys off it to open a tool
    call at all."""
    def canned(request: dict) -> dict:
        return {
            "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t1", "name": "world_say", "input": {"a": 1}}}
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
            "metrics": {"latencyMs": 1},
        }

    gateway = MockBedrockGateway(canned_response_fn=canned)
    events = [
        event
        async for event in gateway.converse_stream(
            {"modelId": "moonshotai.kimi-k2.5", "messages": []}
        )
    ]
    assert "contentBlockStart" in [next(iter(e)) for e in events]


@pytest.mark.asyncio
async def test_converse_stream_can_emit_a_tool_use_block() -> None:
    def canned(request: dict) -> dict:
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "tool_1",
                                "name": "harvest",
                                "input": {"crop": "wheat"},
                            }
                        }
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
            "metrics": {"latencyMs": 1},
        }

    gateway = MockBedrockGateway(canned_response_fn=canned)
    events = [event async for event in gateway.converse_stream({})]

    tool_start = next(e for e in events if "contentBlockStart" in e)
    assert tool_start["contentBlockStart"]["start"]["toolUse"]["name"] == "harvest"
    tool_delta = next(e for e in events if "contentBlockDelta" in e)
    assert "input" in tool_delta["contentBlockDelta"]["delta"]["toolUse"]


@pytest.mark.asyncio
async def test_raise_error_is_one_shot() -> None:
    gateway = MockBedrockGateway()
    gateway.raise_error = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await gateway.converse({})

    response = await gateway.converse({})
    assert response["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_canned_response_fn_overrides_default_text() -> None:
    gateway = MockBedrockGateway(canned_response_fn=lambda req: {
        "output": {"message": {"role": "assistant", "content": [{"text": "custom"}]}},
        "stopReason": "max_tokens",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 0},
    })

    response = await gateway.converse({})

    assert response["output"]["message"]["content"][0]["text"] == "custom"
    assert response["stopReason"] == "max_tokens"
