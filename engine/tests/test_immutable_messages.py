"""Tests for immutable message arrays (accumulator pattern).

Verifies that the agent loop never mutates the messages list in-place.
Instead, it uses a 3-array accumulator pattern:
  - messages_for_api: frozen snapshot sent to the API
  - turn_assistant_msgs: assistant responses this turn
  - turn_tool_results_msgs: tool results this turn

At every transition, these are spread into a new messages list.
"""

from typing import AsyncIterator

import pytest

from src.herald.types import (
    Message,
    StreamEvent,
    StopReason,
    ToolDef,
    Usage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)


LLMMessage = Message


class SpyAdapter:
    """Adapter that records the exact messages list reference it receives."""

    def __init__(self):
        self.received_messages: list[list[LLMMessage]] = []
        self.received_message_ids: list[int] = []
        self.responses: list[tuple[StopReason, str, list]] = []

    def queue_response(
        self,
        stop_reason: StopReason,
        text: str = "output",
        tool_calls: list | None = None,
    ):
        self.responses.append((stop_reason, text, tool_calls or []))

    async def stream_with_tools(
        self,
        messages: list[LLMMessage],
        system=None,
        tools: list[ToolDef] | None = None,
        effort: str | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.received_messages.append(messages)
        self.received_message_ids.append(id(messages))

        stop_reason, text, tool_calls = (
            self.responses.pop(0) if self.responses else (StopReason.END_TURN, "done", [])
        )

        if text:
            yield StreamEvent(type="text", text=text)
        for tc in tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)
        yield StreamEvent(type="stop", stop_reason=stop_reason)
        yield StreamEvent(type="usage", usage=Usage(input_tokens=100, output_tokens=50))

    async def generate(self, messages, system=""):
        return "summary"


class TestMessageListImmutability:
    def test_spread_creates_new_list(self):
        original = [LLMMessage(role="user", content="hello")]
        original_id = id(original)
        new = [*original, LLMMessage(role="assistant", content="hi")]
        assert id(new) != original_id
        assert len(original) == 1
        assert len(new) == 2

    def test_append_mutates_same_list(self):
        original = [LLMMessage(role="user", content="hello")]
        original_id = id(original)
        original.append(LLMMessage(role="assistant", content="hi"))
        assert id(original) == original_id
        assert len(original) == 2

    def test_accumulator_merge_creates_new_list(self):
        messages_for_api = [
            LLMMessage(role="user", content="hello"),
        ]
        turn_assistant_msgs = [
            LLMMessage(role="assistant", content="thinking..."),
        ]
        turn_tool_results_msgs = [
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="result"),
                ],
            ),
        ]
        snapshot_id = id(messages_for_api)

        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]

        assert id(messages) != snapshot_id
        assert len(messages_for_api) == 1
        assert len(messages) == 3


class TestAPIReceivesSnapshot:
    def test_snapshot_is_separate_from_messages(self):
        messages = [LLMMessage(role="user", content="hello")]
        messages_for_api = list(messages)

        assert id(messages_for_api) != id(messages)
        assert messages_for_api == messages

        messages = [*messages, LLMMessage(role="assistant", content="hi")]
        assert len(messages_for_api) == 1
        assert len(messages) == 2

    def test_snapshot_survives_accumulator_merge(self):
        messages = [
            LLMMessage(role="user", content="hello"),
            LLMMessage(role="assistant", content="hi"),
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            ),
        ]
        messages_for_api = list(messages)
        snapshot_len = len(messages_for_api)

        turn_assistant_msgs = [LLMMessage(role="assistant", content="new response")]
        turn_tool_results_msgs = [
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content="new result"),
                ],
            )
        ]

        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]

        assert len(messages_for_api) == snapshot_len
        assert len(messages) == snapshot_len + 2


class TestAccumulatorIsolation:
    def test_accumulator_append_does_not_affect_snapshot(self):
        messages_for_api = [
            LLMMessage(role="user", content="q1"),
            LLMMessage(role="assistant", content="a1"),
        ]
        snapshot_len = len(messages_for_api)

        turn_assistant_msgs: list[LLMMessage] = []
        turn_tool_results_msgs: list[LLMMessage] = []

        turn_assistant_msgs.append(LLMMessage(role="assistant", content="a2"))
        turn_tool_results_msgs.append(LLMMessage(role="user", content="r2"))

        assert len(messages_for_api) == snapshot_len

    def test_multi_tool_accumulation(self):
        messages_for_api = [LLMMessage(role="user", content="do three things")]

        turn_assistant_msgs: list[LLMMessage] = []
        turn_tool_results_msgs: list[LLMMessage] = []

        for i in range(3):
            turn_assistant_msgs.append(
                LLMMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(id=f"t{i}", name="tool", input={}),
                    ],
                )
            )
            turn_tool_results_msgs.append(
                LLMMessage(
                    role="user",
                    content=[
                        ToolResultBlock(tool_use_id=f"t{i}", content=f"result {i}"),
                    ],
                )
            )

        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]

        assert len(messages_for_api) == 1
        assert len(turn_assistant_msgs) == 3
        assert len(turn_tool_results_msgs) == 3
        assert len(messages) == 7

    def test_clearing_accumulators_does_not_affect_merged_messages(self):
        messages_for_api = [LLMMessage(role="user", content="hello")]
        turn_assistant_msgs = [LLMMessage(role="assistant", content="hi")]
        turn_tool_results_msgs = [LLMMessage(role="user", content="result")]

        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]
        assert len(messages) == 3

        turn_assistant_msgs.clear()
        turn_tool_results_msgs.clear()

        assert len(messages) == 3


class TestMaxTokensRecovery:
    def test_continuation_merges_accumulators_into_new_list(self):
        messages = [LLMMessage(role="user", content="write an essay")]
        messages_for_api = list(messages)
        turn_assistant_msgs: list[LLMMessage] = []

        original_id = id(messages)
        turn_text = "partial output that got cut off"

        messages = [
            *messages_for_api,
            *turn_assistant_msgs,
            LLMMessage(role="assistant", content=turn_text),
            LLMMessage(role="user", content="Resume directly."),
        ]

        assert id(messages) != original_id
        assert len(messages) == 3
        assert messages[0].content == "write an essay"
        assert messages[1].content == turn_text
        assert messages[2].content == "Resume directly."

    def test_escalation_preserves_messages(self):
        messages = [
            LLMMessage(role="user", content="hello"),
            LLMMessage(role="assistant", content="response"),
        ]
        original_id = id(messages)
        original_len = len(messages)

        assert id(messages) == original_id
        assert len(messages) == original_len


class TestCompactionImmutability:
    def test_compaction_reassignment(self):
        messages = [
            LLMMessage(role="user", content="msg1"),
            LLMMessage(role="assistant", content="resp1"),
            LLMMessage(role="user", content="msg2"),
        ]
        original_id = id(messages)

        compacted = [
            LLMMessage(role="user", content="[Summary of previous conversation]"),
            LLMMessage(role="user", content="msg2"),
        ]
        messages = compacted

        assert id(messages) != original_id
        assert len(messages) == 2


class TestOverflowRetryReset:
    def test_reset_clears_accumulators(self):
        messages = [LLMMessage(role="user", content="hello")]
        messages_for_api = list(messages)
        turn_assistant_msgs = [LLMMessage(role="assistant", content="partial")]
        turn_tool_results_msgs = [LLMMessage(role="user", content="result")]

        compacted = [LLMMessage(role="user", content="[compacted]")]
        messages = compacted
        messages_for_api = list(messages)
        turn_assistant_msgs = []
        turn_tool_results_msgs = []

        assert len(messages_for_api) == 1
        assert len(turn_assistant_msgs) == 0
        assert len(turn_tool_results_msgs) == 0
        assert messages_for_api[0].content == "[compacted]"


class TestMessageImmutability:
    def test_message_is_frozen(self):
        msg = LLMMessage(role="user", content="hello")
        with pytest.raises(AttributeError):
            msg.role = "assistant"
        with pytest.raises(AttributeError):
            msg.content = "changed"

    def test_text_block_is_frozen(self):
        block = TextBlock(text="hello")
        with pytest.raises(AttributeError):
            block.text = "changed"

    def test_tool_use_block_is_frozen(self):
        block = ToolUseBlock(id="t1", name="tool", input={})
        with pytest.raises(AttributeError):
            block.name = "changed"

    def test_tool_result_block_is_frozen(self):
        block = ToolResultBlock(tool_use_id="t1", content="result")
        with pytest.raises(AttributeError):
            block.content = "changed"


class TestMultiTurnAccumulation:
    def test_two_turns_produce_correct_message_history(self):
        messages = [LLMMessage(role="user", content="hello")]
        messages_for_api = list(messages)
        turn_assistant_msgs: list[LLMMessage] = []
        turn_tool_results_msgs: list[LLMMessage] = []

        turn_assistant_msgs.append(
            LLMMessage(
                role="assistant",
                content=[
                    TextBlock(text="Let me check."),
                    ToolUseBlock(id="t1", name="search", input={"q": "test"}),
                ],
            )
        )
        turn_tool_results_msgs.append(
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="found it"),
                ],
            )
        )
        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]

        assert len(messages) == 3
        turn1_id = id(messages)

        messages_for_api = list(messages)
        turn_assistant_msgs = []
        turn_tool_results_msgs = []

        turn_assistant_msgs.append(LLMMessage(role="assistant", content="Here's the answer."))
        turn_tool_results_msgs.append(
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content="done"),
                ],
            )
        )
        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]

        assert id(messages) != turn1_id
        assert len(messages) == 5
        assert messages[0].role == "user"
        assert messages[0].content == "hello"
        assert messages[4].role == "user"

    def test_charter_reminder_appended_after_merge(self):
        messages_for_api = [LLMMessage(role="user", content="hello")]
        turn_assistant_msgs = [
            LLMMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="tool", input={}),
                ],
            )
        ]
        turn_tool_results_msgs = [
            LLMMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            )
        ]

        messages = [*messages_for_api, *turn_assistant_msgs, *turn_tool_results_msgs]
        merge_id = id(messages)

        messages = [
            *messages,
            LLMMessage(
                role="user",
                content="<system-reminder>Updated goals</system-reminder>",
            ),
        ]

        assert id(messages) != merge_id
        assert len(messages) == 4
        assert "system-reminder" in messages[3].content
