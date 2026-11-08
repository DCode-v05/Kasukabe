"""Anthropic Messages wire <-> Bedrock converse wire translation.

Every request-side test builds fixtures shaped EXACTLY like what
engine/src/herald/provider.py's to_wire_messages / to_wire_tools /
tool_result_to_wire actually produce (see those functions' source,
quoted in translate.py's own docstring) - this file is the contract
test for "herald's base_url can point here unmodified."
"""

from __future__ import annotations

import base64

import pytest

from broker.errors import InvalidRequestError
from broker.translate import (
    anthropic_sse_events_from_converse_stream,
    build_converse_request,
    content_block_to_bedrock,
    converse_response_to_anthropic,
    exact_usage_from_converse_response,
    messages_to_bedrock,
    sse_line,
    system_to_bedrock,
    tool_choice_to_bedrock,
    tools_to_bedrock,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nrest-of-a-fake-png"
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode()


def test_string_content_message_becomes_single_text_block() -> None:
    out = messages_to_bedrock([{"role": "user", "content": "hello"}])
    assert out == [{"role": "user", "content": [{"text": "hello"}]}]


def test_empty_string_content_becomes_empty_block_list() -> None:
    out = messages_to_bedrock([{"role": "user", "content": ""}])
    assert out == [{"role": "user", "content": []}]


def test_text_block_translates_and_drops_cache_control() -> None:
    block = content_block_to_bedrock(
        {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}
    )
    assert block == {"text": "hi"}


def test_tool_use_block_translates() -> None:
    block = content_block_to_bedrock(
        {"type": "tool_use", "id": "toolu_1", "name": "harvest", "input": {"crop": "wheat"}}
    )
    assert block == {"toolUse": {"toolUseId": "toolu_1", "name": "harvest", "input": {"crop": "wheat"}}}


def test_tool_result_block_with_string_content() -> None:
    block = content_block_to_bedrock(
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "42 bushels", "is_error": False}
    )
    assert block == {
        "toolResult": {"toolUseId": "toolu_1", "content": [{"text": "42 bushels"}], "status": "success"}
    }


def test_tool_result_block_error_flag_maps_to_status() -> None:
    block = content_block_to_bedrock(
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "failed", "is_error": True}
    )
    assert block["toolResult"]["status"] == "error"


def test_tool_result_block_with_list_content_text_and_image() -> None:
    block = content_block_to_bedrock(
        {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": [
                {"type": "text", "text": "see attached"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_BASE64}},
            ],
            "is_error": False,
        }
    )
    content = block["toolResult"]["content"]
    assert content[0] == {"text": "see attached"}
    assert content[1]["image"]["format"] == "png"
    assert content[1]["image"]["source"]["bytes"] == PNG_BYTES


def test_image_block_decodes_base64_and_maps_format() -> None:
    block = content_block_to_bedrock(
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": PNG_BASE64}}
    )
    assert block["image"]["format"] == "jpeg"
    assert block["image"]["source"]["bytes"] == PNG_BYTES


def test_image_block_rejects_url_source() -> None:
    with pytest.raises(InvalidRequestError, match="base64"):
        content_block_to_bedrock({"type": "image", "source": {"type": "url", "url": "https://x/y.png"}})


def test_image_block_rejects_unknown_media_type() -> None:
    with pytest.raises(InvalidRequestError, match="media_type"):
        content_block_to_bedrock(
            {"type": "image", "source": {"type": "base64", "media_type": "image/tiff", "data": PNG_BASE64}}
        )


def test_image_block_rejects_invalid_base64() -> None:
    with pytest.raises(InvalidRequestError, match="base64"):
        content_block_to_bedrock(
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "not-base64!!"}}
        )


def test_thinking_block_is_dropped_not_failed() -> None:
    assert content_block_to_bedrock({"type": "thinking", "thinking": "hmm"}) is None
    assert content_block_to_bedrock({"type": "redacted_thinking"}) is None


def test_unknown_block_type_raises_invalid_request() -> None:
    with pytest.raises(InvalidRequestError, match="unknown_block_type"):
        content_block_to_bedrock({"type": "unknown_block_type"})


def test_invalid_message_role_raises() -> None:
    with pytest.raises(InvalidRequestError, match="role"):
        messages_to_bedrock([{"role": "system", "content": "hi"}])


def test_messages_to_bedrock_aggregates_multiple_bad_blocks_in_one_error() -> None:
    with pytest.raises(InvalidRequestError) as excinfo:
        messages_to_bedrock(
            [
                {"role": "user", "content": [{"type": "bogus_a"}]},
                {"role": "assistant", "content": [{"type": "bogus_b"}]},
            ]
        )
    message = str(excinfo.value)
    assert "messages[0]" in message
    assert "messages[1]" in message


def test_system_string_becomes_one_text_block() -> None:
    assert system_to_bedrock("be nice") == [{"text": "be nice"}]


def test_system_none_becomes_empty_list() -> None:
    assert system_to_bedrock(None) == []


def test_system_block_list_drops_cache_control() -> None:
    out = system_to_bedrock(
        [
            {"type": "text", "text": "first", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": "second"},
        ]
    )
    assert out == [{"text": "first"}, {"text": "second"}]


def test_tools_to_bedrock_wraps_in_tool_spec() -> None:
    out = tools_to_bedrock(
        [{"name": "harvest", "description": "Harvest a crop", "input_schema": {"type": "object"}}]
    )
    assert out == [
        {
            "toolSpec": {
                "name": "harvest",
                "description": "Harvest a crop",
                "inputSchema": {"json": {"type": "object"}},
            }
        }
    ]


def test_tools_to_bedrock_truncates_long_names() -> None:
    long_name = "x" * 100
    out = tools_to_bedrock([{"name": long_name, "description": "", "input_schema": {}}])
    assert len(out[0]["toolSpec"]["name"]) == 64


def test_tools_to_bedrock_of_none_is_empty() -> None:
    assert tools_to_bedrock(None) == []


@pytest.mark.parametrize(
    "anthropic_choice,bedrock_choice",
    [
        ({"type": "auto"}, {"auto": {}}),
        ({"type": "any"}, {"any": {}}),
        ({"type": "tool", "name": "harvest"}, {"tool": {"name": "harvest"}}),
    ],
)
def test_tool_choice_translation(anthropic_choice, bedrock_choice) -> None:
    assert tool_choice_to_bedrock(anthropic_choice) == bedrock_choice


def test_tool_choice_none_type_translates_to_none() -> None:
    assert tool_choice_to_bedrock({"type": "none"}) is None


def test_tool_choice_unknown_type_raises() -> None:
    with pytest.raises(InvalidRequestError):
        tool_choice_to_bedrock({"type": "bogus"})


def test_build_converse_request_assembles_core_fields() -> None:
    request = build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=[{"role": "user", "content": "hi"}],
        system="be nice",
        max_tokens=512,
        temperature=0.7,
        top_p=0.9,
        stop_sequences=["STOP"],
    )
    assert request["modelId"] == "moonshotai.kimi-k2.5"
    assert request["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert request["system"] == [{"text": "be nice"}]
    assert request["inferenceConfig"] == {
        "maxTokens": 512,
        "temperature": 0.7,
        "topP": 0.9,
        "stopSequences": ["STOP"],
    }
    assert "toolConfig" not in request


def test_build_converse_request_omits_system_when_absent() -> None:
    request = build_converse_request(
        model="moonshotai.kimi-k2.5", messages=[], system=None, max_tokens=10
    )
    assert "system" not in request


def test_build_converse_request_includes_tool_config_when_tools_present() -> None:
    request = build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=[],
        system=None,
        max_tokens=10,
        tools=[{"name": "harvest", "description": "d", "input_schema": {}}],
        tool_choice={"type": "auto"},
    )
    assert request["toolConfig"]["tools"][0]["toolSpec"]["name"] == "harvest"
    assert request["toolConfig"]["toolChoice"] == {"auto": {}}


def test_build_converse_request_tool_choice_none_drops_tools_entirely() -> None:
    request = build_converse_request(
        model="moonshotai.kimi-k2.5",
        messages=[],
        system=None,
        max_tokens=10,
        tools=[{"name": "harvest", "description": "d", "input_schema": {}}],
        tool_choice={"type": "none"},
    )
    assert "toolConfig" not in request


def test_build_converse_request_top_k_goes_to_additional_fields() -> None:
    request = build_converse_request(
        model="moonshotai.kimi-k2.5", messages=[], system=None, max_tokens=10, top_k=40
    )
    assert request["additionalModelRequestFields"] == {"top_k": 40}


def test_converse_response_to_anthropic_text() -> None:
    response = {
        "output": {"message": {"role": "assistant", "content": [{"text": "hi there"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
    }
    out = converse_response_to_anthropic(response, model="moonshotai.kimi-k2.5")

    assert out["role"] == "assistant"
    assert out["model"] == "moonshotai.kimi-k2.5"
    assert out["content"] == [{"type": "text", "text": "hi there"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert out["id"].startswith("msg_")


def test_converse_response_to_anthropic_tool_use() -> None:
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "t1", "name": "harvest", "input": {"crop": "corn"}}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
    }
    out = converse_response_to_anthropic(response, model="deepseek.v3.2")
    assert out["content"] == [{"type": "tool_use", "id": "t1", "name": "harvest", "input": {"crop": "corn"}}]
    assert out["stop_reason"] == "tool_use"


@pytest.mark.parametrize(
    "bedrock_reason,anthropic_reason",
    [
        ("end_turn", "end_turn"),
        ("max_tokens", "max_tokens"),
        ("stop_sequence", "stop_sequence"),
        ("content_filtered", "refusal"),
        ("guardrail_intervened", "refusal"),
        ("something_new_bedrock_added", "end_turn"),
    ],
)
def test_stop_reason_mapping(bedrock_reason, anthropic_reason) -> None:
    response = {
        "output": {"message": {"role": "assistant", "content": []}},
        "stopReason": bedrock_reason,
        "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
    }
    out = converse_response_to_anthropic(response, model="moonshotai.kimi-k2.5")
    assert out["stop_reason"] == anthropic_reason


def test_exact_usage_from_converse_response() -> None:
    response = {"usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10}}
    assert exact_usage_from_converse_response(response) == (7, 3)


def test_exact_usage_defaults_to_zero_when_missing() -> None:
    assert exact_usage_from_converse_response({}) == (0, 0)


async def bedrock_events(events: list[dict]):
    for e in events:
        yield e


@pytest.mark.asyncio
async def test_stream_translation_full_text_sequence() -> None:
    raw = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "hel"}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "lo"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 12, "outputTokens": 2, "totalTokens": 14}, "metrics": {}}},
    ]
    captured_usage = {}
    events = [
        e
        async for e in anthropic_sse_events_from_converse_stream(
            bedrock_events(raw),
            model="moonshotai.kimi-k2.5",
            estimated_input_tokens=99,
            on_usage=lambda i, o: captured_usage.update(input_tokens=i, output_tokens=o),
        )
    ]

    types = [e["type"] for e in events]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_delta",
        "message_stop",
    ]
    assert events[0]["message"]["usage"]["input_tokens"] == 99
    assert events[2]["delta"] == {"type": "text_delta", "text": "hel"}
    assert events[3]["delta"] == {"type": "text_delta", "text": "lo"}
    assert events[5]["delta"]["stop_reason"] == "end_turn"
    assert events[6]["usage"]["output_tokens"] == 2
    assert captured_usage == {"input_tokens": 12, "output_tokens": 2}


@pytest.mark.asyncio
async def test_stream_translation_tool_use_sequence() -> None:
    raw = [
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "t1", "name": "harvest"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"crop":'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '"corn"}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10}, "metrics": {}}},
    ]
    events = [
        e
        async for e in anthropic_sse_events_from_converse_stream(
            bedrock_events(raw), model="moonshotai.kimi-k2.5", estimated_input_tokens=1
        )
    ]

    start_event = next(e for e in events if e["type"] == "content_block_start")
    assert start_event["content_block"] == {"type": "tool_use", "id": "t1", "name": "harvest", "input": {}}
    deltas = [e for e in events if e["type"] == "content_block_delta"]
    assert deltas[0]["delta"] == {"type": "input_json_delta", "partial_json": '{"crop":'}
    assert deltas[1]["delta"] == {"type": "input_json_delta", "partial_json": '"corn"}'}


@pytest.mark.asyncio
async def test_stream_translation_without_on_usage_callback_does_not_raise() -> None:
    raw = [{"messageStart": {"role": "assistant"}}, {"metadata": {"usage": {}, "metrics": {}}}]
    events = [
        e
        async for e in anthropic_sse_events_from_converse_stream(
            bedrock_events(raw), model="moonshotai.kimi-k2.5", estimated_input_tokens=0
        )
    ]
    assert events[0]["type"] == "message_start"


def test_sse_line_shape() -> None:
    line = sse_line({"type": "message_stop"})
    assert line == 'event: message_stop\ndata: {"type": "message_stop"}\n\n'


def test_tool_config_is_sent_when_the_transcript_carries_tool_blocks():
    """Bedrock rejects a request whose messages carry toolUse/toolResult
    unless `toolConfig` is present - even when this turn offers no tools:

        ValidationException: The toolConfig field must be defined when using
        toolUse and toolResult content blocks.

    That is what every continuation turn looks like, and it was the cause of
    **every** failure in a 1,559-call live run: 150 of them, ~10% of traffic,
    one bug.
    """
    from broker.translate import build_converse_request

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "harvest", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    request = build_converse_request("m", messages, None, 100)

    assert "toolConfig" in request
    assert [t["toolSpec"]["name"] for t in request["toolConfig"]["tools"]] == ["harvest"]


def test_a_plain_turn_still_sends_no_tool_config():
    """The repair must not attach tools to a conversation that never used
    any - that would offer the model capability nobody granted it."""
    from broker.translate import build_converse_request

    request = build_converse_request(
        "m", [{"role": "user", "content": [{"type": "text", "text": "hello"}]}], None, 100
    )
    assert "toolConfig" not in request


def test_the_repair_only_names_tools_the_model_actually_called():
    """Recovered from the transcript, not invented: a tool named here is one
    the model already called, so this restores a fact rather than granting
    new capability."""
    from broker.translate import tools_referenced_in

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "1", "name": "mine", "input": {}},
            {"type": "tool_use", "id": "2", "name": "say", "input": {}},
            {"type": "tool_use", "id": "3", "name": "mine", "input": {}}]},
    ]
    names = [t["toolSpec"]["name"] for t in tools_referenced_in(messages)]

    assert names == ["mine", "say"], "deduplicated, order preserved"
    assert tools_referenced_in([]) == []


def test_a_reasoning_block_becomes_a_thinking_block():
    from broker.translate import content_block_from_bedrock

    block = content_block_from_bedrock({
        "reasoningContent": {
            "reasoningText": {"text": "Marta is owed 400. Ask before she does.",
                              "signature": "sig-abc"}
        }
    })
    assert block == {
        "type": "thinking",
        "thinking": "Marta is owed 400. Ask before she does.",
        "signature": "sig-abc",
    }


def test_the_flatter_reasoning_shape_is_read_too():
    """Bedrock nests the text differently per model."""
    from broker.translate import content_block_from_bedrock

    assert content_block_from_bedrock(
        {"reasoningContent": {"text": "thinking out loud"}}
    )["thinking"] == "thinking out loud"
    assert content_block_from_bedrock(
        {"reasoningContent": {"reasoningText": "a bare string"}}
    )["thinking"] == "a bare string"


def test_a_redacted_reasoning_block_is_dropped_not_faked():
    """Inventing an empty thinking block would make "did not think" and
    "was not allowed to show it" look the same again."""
    from broker.translate import content_block_from_bedrock

    assert content_block_from_bedrock({"reasoningContent": {}}) is None
    assert content_block_from_bedrock(
        {"reasoningContent": {"reasoningText": {"text": ""}}}) is None


def test_an_unknown_block_kind_is_still_dropped():
    from broker.translate import content_block_from_bedrock

    assert content_block_from_bedrock({"somethingNewFromBedrock": {"x": 1}}) is None
