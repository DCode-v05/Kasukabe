"""Tests for ThinkingBlock type and strip_thinking_blocks logic."""

from __future__ import annotations

from src.herald.types import (
    ContentBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)


def strip_thinking_blocks(messages: list[Message]) -> list[Message]:
    """Local copy of the strip logic for testing without heavy thread.py imports."""
    result = []
    for msg in messages:
        if isinstance(msg.content, str):
            result.append(msg)
            continue
        filtered = [b for b in msg.content if not isinstance(b, ThinkingBlock)]
        if len(filtered) == len(msg.content):
            result.append(msg)
        else:
            result.append(Message(role=msg.role, content=filtered or ""))
    return result


def test_thinking_block_in_content_block_union():
    tb = ThinkingBlock(thinking="reasoning here")
    assert isinstance(tb, ThinkingBlock)
    blocks: list[ContentBlock] = [tb, TextBlock(text="hello")]
    assert len(blocks) == 2


def test_thinking_block_redacted_flag():
    normal = ThinkingBlock(thinking="visible reasoning")
    assert not normal.is_redacted
    redacted = ThinkingBlock(thinking="signature_data", is_redacted=True)
    assert redacted.is_redacted


def test_strip_removes_thinking_blocks():
    messages = [
        Message(
            role="assistant",
            content=[
                ThinkingBlock(thinking="step 1"),
                TextBlock(text="answer"),
                ToolUseBlock(id="t1", name="read", input={}),
            ],
        ),
    ]
    result = strip_thinking_blocks(messages)
    assert len(result) == 1
    assert len(result[0].content) == 2
    assert isinstance(result[0].content[0], TextBlock)
    assert isinstance(result[0].content[1], ToolUseBlock)


def test_strip_removes_redacted_thinking():
    messages = [
        Message(
            role="assistant",
            content=[
                ThinkingBlock(thinking="sig", is_redacted=True),
                TextBlock(text="response"),
            ],
        ),
    ]
    result = strip_thinking_blocks(messages)
    assert len(result[0].content) == 1
    assert isinstance(result[0].content[0], TextBlock)


def test_strip_passes_through_no_thinking():
    messages = [
        Message(role="user", content="hello"),
        Message(role="assistant", content=[TextBlock(text="hi")]),
    ]
    result = strip_thinking_blocks(messages)
    assert result[0] is messages[0]
    assert result[1] is messages[1]


def test_strip_handles_string_content():
    messages = [
        Message(role="user", content="just text"),
        Message(role="assistant", content="also text"),
    ]
    result = strip_thinking_blocks(messages)
    assert result == messages


def test_strip_handles_all_thinking_removed():
    messages = [
        Message(
            role="assistant",
            content=[
                ThinkingBlock(thinking="only thinking"),
            ],
        ),
    ]
    result = strip_thinking_blocks(messages)
    assert result[0].content == ""
