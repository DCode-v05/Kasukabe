"""Unit tests for src/engine_core/time_based_microcompact.py.

These tests are the behavior contract for the time-based and forced
microcompact strategies. They exercise the trigger evaluation, the
content-clearing algorithm, the keep-recent floor, the staleness marker,
tool-use/tool-result pairing preservation, and input immutability.

No LLM calls, no SQLite — pure in-memory dataclasses.
"""

from __future__ import annotations

import time
import pytest

from src.engine_core.microcompact_config import MicrocompactConfig, COMPACTABLE_TOOLS
from src.engine_core.prompt_cache_break_detection import (
    consume_pending_cache_deletion,
    is_cache_deletion_pending,
    notify_cache_deletion,
    reset_pending_deletions,
)
from src.engine_core.time_based_microcompact import (
    IMAGE_MAX_TOKEN_SIZE,
    TriggerMode,
    MicrocompactResult,
    STALENESS_MARKER,
    evaluate_trigger,
    maybe_microcompact,
)
from src.herald.types import ImageBlock, Message, TextBlock, ToolUseBlock, ToolResultBlock


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset cache-deletion module state between tests so a fire in one
    test doesn't leak into the next.
    """
    reset_pending_deletions()
    yield
    reset_pending_deletions()


def assistant_with_tool_use(tool_name: str, use_id: str, ts: float | None) -> Message:
    return Message(
        role="assistant",
        content=[
            TextBlock(text=f"calling {tool_name}"),
            ToolUseBlock(id=use_id, name=tool_name, input={}),
        ],
        timestamp=ts,
    )


def user_with_tool_result(use_id: str, content: str, ts: float | None) -> Message:
    return Message(
        role="user",
        content=[
            ToolResultBlock(tool_use_id=use_id, content=content, is_error=False),
        ],
        timestamp=ts,
    )


def build_paired_messages(
    n: int,
    tool_name: str = "Read",
    base_ts: float | None = None,
    content_prefix: str = "result-",
) -> list[Message]:
    """Build n paired (assistant tool_use / user tool_result) messages.

    Each pair shares a timestamp. The last assistant timestamp is set to
    ``base_ts`` if provided, otherwise the current wall-clock.
    """
    if base_ts is None:
        base_ts = time.time()
    msgs: list[Message] = []
    for i in range(n):
        uid = f"u{i}"
        msgs.append(assistant_with_tool_use(tool_name, uid, base_ts))
        msgs.append(
            user_with_tool_result(
                uid,
                content=f"{content_prefix}{i}" * 100,
                ts=base_ts,
            )
        )
    return msgs


def cfg(enabled: bool = True, gap: float = 60.0, keep: int = 5) -> MicrocompactConfig:
    return MicrocompactConfig(
        enabled=enabled,
        gap_threshold_minutes=gap,
        keep_recent=keep,
    )


class TestEvaluateTrigger:
    def test_disabled_returns_none(self):
        stale_ts = time.time() - 3600
        msgs = build_paired_messages(2, base_ts=stale_ts)
        assert evaluate_trigger(msgs, cfg(enabled=False), TriggerMode.TIME_BASED) is None

    def test_no_assistant_message_returns_none(self):
        msgs = [Message(role="user", content="hi", timestamp=time.time() - 3600)]
        assert evaluate_trigger(msgs, cfg(), TriggerMode.TIME_BASED) is None

    def test_no_timestamp_on_assistant_returns_none(self):
        msgs = [
            Message(role="user", content="hi", timestamp=time.time() - 3600),
            Message(role="assistant", content="hello", timestamp=None),
        ]
        assert evaluate_trigger(msgs, cfg(), TriggerMode.TIME_BASED) is None

    def test_below_threshold_returns_none(self):
        recent_ts = time.time() - (30 * 60)
        msgs = [Message(role="assistant", content="hello", timestamp=recent_ts)]
        assert evaluate_trigger(msgs, cfg(gap=60.0), TriggerMode.TIME_BASED) is None

    def test_above_threshold_fires(self):
        stale_ts = time.time() - (90 * 60)
        msgs = [Message(role="assistant", content="hello", timestamp=stale_ts)]
        trig = evaluate_trigger(msgs, cfg(gap=60.0), TriggerMode.TIME_BASED)
        assert trig is not None
        assert trig.mode is TriggerMode.TIME_BASED
        assert trig.gap_minutes >= 60.0

    def test_at_threshold_fires(self):
        exact_ts = time.time() - (60 * 60 + 1)
        msgs = [Message(role="assistant", content="hello", timestamp=exact_ts)]
        trig = evaluate_trigger(msgs, cfg(gap=60.0), TriggerMode.TIME_BASED)
        assert trig is not None

    def test_walks_backward_to_find_last_timestamped_assistant(self):
        stale_ts = time.time() - (120 * 60)
        msgs = [
            Message(role="user", content="hi", timestamp=time.time()),
            Message(role="assistant", content="old", timestamp=stale_ts),
            Message(role="user", content="hi again", timestamp=time.time()),
            Message(role="assistant", content="newer", timestamp=None),
        ]
        trig = evaluate_trigger(msgs, cfg(gap=60.0), TriggerMode.TIME_BASED)
        assert trig is not None
        assert trig.gap_minutes >= 60.0

    def test_forced_mode_fires_regardless_of_gap(self):
        fresh_ts = time.time()
        msgs = [Message(role="assistant", content="hi", timestamp=fresh_ts)]
        trig = evaluate_trigger(msgs, cfg(), TriggerMode.FORCED)
        assert trig is not None
        assert trig.mode is TriggerMode.FORCED

    def test_forced_mode_still_respects_enabled(self):
        msgs = [Message(role="assistant", content="hi", timestamp=time.time())]
        assert evaluate_trigger(msgs, cfg(enabled=False), TriggerMode.FORCED) is None

    def test_forced_mode_needs_no_timestamp(self):
        msgs = [Message(role="assistant", content="hi", timestamp=None)]
        trig = evaluate_trigger(msgs, cfg(), TriggerMode.FORCED)
        assert trig is not None


class TestMaybeMicrocompact:
    def test_below_threshold_returns_none(self):
        recent_ts = time.time() - (10 * 60)
        msgs = build_paired_messages(5, base_ts=recent_ts)
        assert maybe_microcompact(msgs, cfg(gap=60.0), TriggerMode.TIME_BASED) is None

    def test_keeps_last_n_results(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(10, base_ts=stale_ts)
        result = maybe_microcompact(
            msgs,
            cfg(keep=3),
            TriggerMode.TIME_BASED,
        )
        assert result is not None
        assert result.tools_kept == 3
        assert result.tools_cleared == 7

    def test_keep_recent_floored_at_one(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        result = maybe_microcompact(
            msgs,
            cfg(keep=0),
            TriggerMode.TIME_BASED,
        )
        assert result is not None
        assert result.tools_kept == 1
        assert result.tools_cleared == 4

    def test_ignores_non_compactable_tools(self):
        stale_ts = time.time() - (120 * 60)
        assert "MemoryWrite" not in COMPACTABLE_TOOLS
        msgs = [
            assistant_with_tool_use("MemoryWrite", "u0", stale_ts),
            user_with_tool_result("u0", "persistent state" * 100, stale_ts),
        ]
        result = maybe_microcompact(msgs, cfg(), TriggerMode.TIME_BASED)
        assert result is None

    def test_only_compactable_results_are_cleared(self):
        stale_ts = time.time() - (120 * 60)
        msgs: list[Message] = []
        for i in range(6):
            tool = "Read" if i % 2 == 0 else "MemoryWrite"
            uid = f"u{i}"
            msgs.append(assistant_with_tool_use(tool, uid, stale_ts))
            msgs.append(user_with_tool_result(uid, f"content-{i}" * 100, stale_ts))
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert result.tools_kept == 1
        assert result.tools_cleared == 2

    def test_cleared_content_uses_staleness_marker(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None

        cleared_contents = []
        for m in result.messages:
            if not isinstance(m.content, list):
                continue
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    body = b.content if isinstance(b.content, str) else ""
                    cleared_contents.append(body)
        n_cleared = sum(1 for c in cleared_contents if c == STALENESS_MARKER)
        assert n_cleared == 4

    def test_preserves_tool_use_id_and_is_error(self):
        stale_ts = time.time() - (120 * 60)
        msgs: list[Message] = [
            assistant_with_tool_use("Read", "keep-me", stale_ts),
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="keep-me",
                        content="big content" * 100,
                        is_error=True,
                    )
                ],
                timestamp=stale_ts,
            ),
            assistant_with_tool_use("Read", "also-keep", stale_ts),
            user_with_tool_result("also-keep", "another" * 100, stale_ts),
        ]
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        for m in result.messages:
            if not isinstance(m.content, list):
                continue
            for b in m.content:
                if isinstance(b, ToolResultBlock) and b.tool_use_id == "keep-me":
                    assert b.content == STALENESS_MARKER
                    assert b.is_error is True

    def test_preserves_message_timestamp(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        for m in result.messages:
            assert m.timestamp is not None
            assert m.timestamp == pytest.approx(stale_ts, abs=1e-6)

    def test_does_not_mutate_input(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        original_ids = [id(m) for m in msgs]
        original_contents = [
            b.content
            for m in msgs
            if isinstance(m.content, list)
            for b in m.content
            if isinstance(b, ToolResultBlock)
        ]
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert [id(m) for m in msgs] == original_ids
        new_contents = [
            b.content
            for m in msgs
            if isinstance(m.content, list)
            for b in m.content
            if isinstance(b, ToolResultBlock)
        ]
        assert new_contents == original_contents

    def test_tokens_saved_is_positive(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts, content_prefix="lots-of-text-")
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert result.tokens_saved > 0

    def test_no_stale_results_to_clear_returns_none(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(3, base_ts=stale_ts)
        result = maybe_microcompact(msgs, cfg(keep=5), TriggerMode.TIME_BASED)
        assert result is None

    def test_forced_mode_clears_even_when_fresh(self):
        fresh_ts = time.time()
        msgs = build_paired_messages(8, base_ts=fresh_ts)
        result = maybe_microcompact(msgs, cfg(keep=2), TriggerMode.FORCED)
        assert result is not None
        assert result.tools_kept == 2
        assert result.tools_cleared == 6

    def test_disabled_config_blocks_forced_mode(self):
        fresh_ts = time.time()
        msgs = build_paired_messages(5, base_ts=fresh_ts)
        result = maybe_microcompact(
            msgs,
            cfg(enabled=False),
            TriggerMode.FORCED,
        )
        assert result is None

    def test_non_tool_messages_are_untouched(self):
        stale_ts = time.time() - (120 * 60)
        msgs: list[Message] = [
            Message(role="user", content="plain text", timestamp=stale_ts),
            *build_paired_messages(4, base_ts=stale_ts),
            Message(role="user", content="another plain", timestamp=stale_ts),
        ]
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert result.messages[0].content == "plain text"
        assert result.messages[-1].content == "another plain"

    def test_returns_correct_shape(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert isinstance(result, MicrocompactResult)
        assert isinstance(result.messages, list)
        assert result.tools_kept >= 0
        assert result.tools_cleared >= 0
        assert result.tokens_saved >= 0
        assert result.gap_minutes >= 0


class TestAlreadyClearedGuard:
    def test_second_pass_is_noop_on_fully_cleared_conversation(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        first = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert first is not None
        assert first.tools_cleared == 4

        second = maybe_microcompact(first.messages, cfg(keep=1), TriggerMode.TIME_BASED)
        assert second is None, "second pass must be a no-op once all eligible results are cleared"

    def test_second_pass_clears_new_results_only(self):
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        first = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert first is not None

        new_msgs = list(first.messages)
        for i in range(3):
            uid = f"new-{i}"
            new_msgs.append(assistant_with_tool_use("Read", uid, stale_ts))
            new_msgs.append(user_with_tool_result(uid, f"new-content-{i}" * 100, stale_ts))

        second = maybe_microcompact(new_msgs, cfg(keep=2), TriggerMode.TIME_BASED)
        assert second is not None
        assert second.tools_kept == 2
        assert second.tools_cleared == 2, (
            "already-cleared blocks must not be counted in tools_cleared"
        )

    def test_collected_filters_already_cleared_marker_string(self):
        stale_ts = time.time() - (120 * 60)
        msgs: list[Message] = []
        for i in range(4):
            uid = f"u{i}"
            msgs.append(assistant_with_tool_use("Read", uid, stale_ts))
            content = STALENESS_MARKER if i < 2 else f"live-{i}" * 50
            msgs.append(
                Message(
                    role="user",
                    content=[ToolResultBlock(tool_use_id=uid, content=content, is_error=False)],
                    timestamp=stale_ts,
                )
            )
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert result.tools_kept == 1
        assert result.tools_cleared == 1


class TestImageTokenCounting:
    def test_image_blocks_contribute_to_tokens_saved(self):
        stale_ts = time.time() - (120 * 60)
        image_block = ImageBlock(
            source={
                "type": "base64",
                "media_type": "image/png",
                "data": "fake-data",
            }
        )
        msgs: list[Message] = [
            assistant_with_tool_use("Read", "u0", stale_ts),
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="u0",
                        content=[TextBlock(text="see image:"), image_block],
                        is_error=False,
                    )
                ],
                timestamp=stale_ts,
            ),
            assistant_with_tool_use("Read", "u1", stale_ts),
            user_with_tool_result("u1", "plain text" * 50, stale_ts),
        ]
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert result.tools_cleared == 1
        assert result.tokens_saved >= IMAGE_MAX_TOKEN_SIZE


class TestCacheBreakNotificationSeam:
    def test_initial_state_no_pending(self):
        assert is_cache_deletion_pending() is False

    def test_notify_sets_pending(self):
        notify_cache_deletion()
        assert is_cache_deletion_pending() is True

    def test_consume_clears_pending(self):
        notify_cache_deletion()
        assert consume_pending_cache_deletion() is True
        assert is_cache_deletion_pending() is False

    def test_consume_is_false_when_nothing_pending(self):
        assert consume_pending_cache_deletion() is False

    def test_per_source_isolation(self):
        notify_cache_deletion("main")
        assert consume_pending_cache_deletion("sub-agent-1") is False
        assert consume_pending_cache_deletion("main") is True

    def test_microcompact_fire_notifies_cache_deletion(self):
        assert is_cache_deletion_pending() is False
        stale_ts = time.time() - (120 * 60)
        msgs = build_paired_messages(5, base_ts=stale_ts)
        result = maybe_microcompact(msgs, cfg(keep=1), TriggerMode.TIME_BASED)
        assert result is not None
        assert is_cache_deletion_pending() is True

    def test_microcompact_noop_does_not_notify(self):
        assert is_cache_deletion_pending() is False
        fresh_ts = time.time()
        msgs = build_paired_messages(3, base_ts=fresh_ts)
        result = maybe_microcompact(msgs, cfg(keep=5), TriggerMode.TIME_BASED)
        assert result is None
        assert is_cache_deletion_pending() is False
