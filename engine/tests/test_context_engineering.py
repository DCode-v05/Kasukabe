import pytest
from src.engine_core.context_engineering import (
    estimate_tokens,
    estimate_tokens_dense,
    cap_tool_result,
    cap_turn_tool_results,
    cap_sub_agent_result,
    needs_compaction,
    compact_messages,
    emergency_compact,
    repair_tool_use_result_pairing,
    generate_tombstone_results,
    guard_tool_results,
    gut_tool_results,
    extraction_stub,
    is_compacted_stub,
    snip_prepass,
    strip_media_from_messages,
    format_compact_summary,
    extract_token_gap,
    drop_oldest_rounds,
    COMPACT_PROMPT,
    PTL_RETRY_MARKER,
    COMPACTION_THRESHOLD,
    PRESERVE_RECENT,
)
from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 1

    def test_short_text(self):
        tokens = estimate_tokens("hello")
        assert tokens >= 1

    def test_longer_text_scales(self):
        short = estimate_tokens("hello")
        long = estimate_tokens("hello " * 100)
        assert long > short

    def test_dense_higher_than_normal(self):
        text = '{"key": "value", "nested": {"a": 1}}'
        assert estimate_tokens_dense(text) > estimate_tokens(text)


class TestCapToolResult:
    def test_short_result_unchanged(self):
        short = "This is a short result."
        assert cap_tool_result(short) == short

    def test_long_result_truncated(self):
        long_text = "x" * 100_000
        capped = cap_tool_result(long_text)
        assert len(capped) < len(long_text)
        assert "truncated" in capped.lower()

    def test_error_tail_preserves_both_ends(self):
        head = "a" * 50_000
        tail = "\nTraceback (most recent call last):\n  File 'test.py'\nError: something failed"
        long_text = head + tail
        capped = cap_tool_result(long_text)
        assert "middle truncated" in capped.lower()
        assert "Error" in capped


class TestCapSubAgentResult:
    def test_short_unchanged(self):
        short = "Done."
        assert cap_sub_agent_result(short) == short

    def test_long_truncated(self):
        long_text = "word " * 10_000
        capped = cap_sub_agent_result(long_text)
        assert len(capped) < len(long_text)
        assert capped.endswith("...")


class TestNeedsCompaction:
    def test_at_threshold(self):
        window = 200_000
        at_80_pct = int(window * COMPACTION_THRESHOLD)
        assert needs_compaction(at_80_pct, window) is True

    def test_below_threshold(self):
        window = 200_000
        below = int(window * COMPACTION_THRESHOLD) - 1
        assert needs_compaction(below, window) is False

    def test_over_threshold(self):
        assert needs_compaction(190_000, 200_000) is True

    def test_zero(self):
        assert needs_compaction(0, 200_000) is False


class TestCompactMessages:
    def make_messages(self, count: int) -> list[Message]:
        return [
            Message(role="user" if i % 2 == 0 else "assistant", content=f"Message {i}")
            for i in range(count)
        ]

    def test_under_budget_unchanged(self):
        msgs = self.make_messages(5)
        result = compact_messages(msgs, context_window=200_000)
        assert len(result) == 5

    def test_preserves_recent(self):
        msgs = self.make_messages(30)
        result = compact_messages(msgs, context_window=100, preserve_recent=15)
        assert len(result) <= len(msgs)
        assert result[-1].content == msgs[-1].content

    def test_guts_old_tool_results(self):
        old_tool_result = Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content="Very long result " * 500),
            ],
        )
        recent = [Message(role="user", content=f"Recent {i}") for i in range(15)]
        msgs = [old_tool_result] + recent
        result = compact_messages(msgs, context_window=100, preserve_recent=15)
        if len(result) > 0 and isinstance(result[0].content, list):
            for block in result[0].content:
                if isinstance(block, ToolResultBlock):
                    assert block.content == "[trimmed]"


class TestEmergencyCompact:
    def test_reduces_message_count(self):
        msgs = [
            Message(role="user" if i % 2 == 0 else "assistant", content=f"Message {i} " * 100)
            for i in range(20)
        ]
        result = emergency_compact(msgs, context_window=200_000, preserve_recent=5)
        assert len(result) <= 6

    def test_keeps_first_and_last(self):
        msgs = [Message(role="user", content=f"Message {i}") for i in range(20)]
        result = emergency_compact(msgs, context_window=200_000, preserve_recent=5)
        assert result[0].content == msgs[0].content
        assert result[-1].content == msgs[-1].content

    def test_guts_all_tool_results(self):
        msgs = [
            Message(role="user", content="first"),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="Big result " * 100),
                ],
            ),
            Message(role="assistant", content="response"),
        ]
        result = emergency_compact(msgs, context_window=200_000, preserve_recent=5)
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        assert block.content == "[trimmed]"

    def test_round_safe_slicing(self):
        msgs = [
            Message(role="user", content="start"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="bash", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="output " * 50),
                ],
            ),
            Message(role="assistant", content="mid", api_message_id="msg_002"),
            Message(role="user", content="question"),
            Message(role="assistant", content="last", api_message_id="msg_003"),
        ]
        result = emergency_compact(msgs, context_window=200_000, preserve_recent=2)
        for i, msg in enumerate(result):
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        assert i + 1 < len(result)
                        next_msg = result[i + 1]
                        assert isinstance(next_msg.content, list)
                        result_ids = {
                            b.tool_use_id
                            for b in next_msg.content
                            if isinstance(b, ToolResultBlock)
                        }
                        assert block.id in result_ids or "[error]" in str(next_msg.content)


class TestRepairToolUseResultPairing:
    def test_no_orphans_unchanged(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={"text": "hi"}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="hi"),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        assert len(result) == 2

    def test_orphaned_tool_use_gets_synthetic_result(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={"text": "hi"}),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        has_synthetic = False
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id == "t1":
                        has_synthetic = True
                        assert "interrupted" in block.content.lower()
        assert has_synthetic

    def test_orphaned_result_dropped(self):
        msgs = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="orphan-id", content="stale result"),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    assert not isinstance(block, ToolResultBlock)

    def test_mixed_orphans(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="matched"),
                    ToolResultBlock(tool_use_id="orphan", content="stale"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="search", input={}),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        all_result_ids = set()
        all_use_ids = set()
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        all_result_ids.add(block.tool_use_id)
                    elif isinstance(block, ToolUseBlock):
                        all_use_ids.add(block.id)
        assert "t1" in all_result_ids
        assert "t2" in all_result_ids
        assert "orphan" not in all_result_ids


class TestRepairToolUseResultPairingExtended:
    def test_is_error_true_on_synthetic_results(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id == "t1":
                        assert block.is_error is True
                        assert "interrupted" in block.content.lower()

    def test_cross_message_duplicate_tool_use_stripped(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="dup", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="dup", content="ok"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="dup", name="echo", input={}),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        use_count = sum(
            1
            for msg in result
            if isinstance(msg.content, list)
            for b in msg.content
            if isinstance(b, ToolUseBlock) and b.id == "dup"
        )
        assert use_count == 1

    def test_orphaned_results_at_conversation_start(self):
        msgs = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="stale", content="old result"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        first_msg = result[0]
        assert isinstance(first_msg.content, list)
        for block in first_msg.content:
            assert not isinstance(block, ToolResultBlock)

    def test_orphaned_results_at_non_zero_position(self):
        """User message with tool_results where the preceding output message
        is not an assistant (e.g. two user messages adjacent after compaction)."""
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content=[TextBlock(text="ok")]),
            Message(role="user", content="follow up"),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="stale", content="orphaned mid-conversation"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        assert block.tool_use_id != "stale"

    def test_duplicate_tool_result_dedup_in_same_user_msg(self):
        """Duplicate tool_result IDs in the same user message (normalizeMessagesForAPI merge artifact)."""
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="first"),
                    ToolResultBlock(tool_use_id="t1", content="duplicate"),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        result_count = sum(
            1
            for msg in result
            if isinstance(msg.content, list)
            for b in msg.content
            if isinstance(b, ToolResultBlock) and b.tool_use_id == "t1"
        )
        assert result_count == 1

    def test_synthetic_prepended_into_adjacent_user_msg(self):
        """Synthetic results should be prepended into the existing adjacent user message,
        not appended as a new message at the end."""
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                    ToolUseBlock(id="t2", name="search", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        assert len(result) == 2
        user_msg = result[1]
        assert user_msg.role == "user"
        result_ids = [b.tool_use_id for b in user_msg.content if isinstance(b, ToolResultBlock)]
        assert "t2" in result_ids
        assert "t1" in result_ids
        assert result_ids.index("t2") < result_ids.index("t1")

    def test_empty_content_gets_placeholder_not_dropped(self):
        msgs = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="orphan", content="stale"),
                ],
            ),
            Message(role="assistant", content=[TextBlock(text="hello")]),
        ]
        result = repair_tool_use_result_pairing(msgs)
        assert len(result) >= 1

    def test_role_alternation_preserved(self):
        msgs = [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="matched"),
                    ToolResultBlock(tool_use_id="orphan", content="stale"),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="search", input={}),
                ],
            ),
        ]
        result = repair_tool_use_result_pairing(msgs)
        prev_role = None
        for msg in result:
            if prev_role is not None:
                assert msg.role != prev_role, f"Role alternation broken: consecutive {msg.role}"
            prev_role = msg.role


class TestGenerateTombstoneResults:
    def test_creates_one_message_per_orphan(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                    ToolUseBlock(id="t2", name="search", input={}),
                ],
            ),
        ]
        tombstones = generate_tombstone_results(msgs, "[Cancelled by user]")
        assert len(tombstones) == 2
        for tomb_msg in tombstones:
            assert tomb_msg.role == "user"
            assert isinstance(tomb_msg.content, list)
            assert len(tomb_msg.content) == 1
            block = tomb_msg.content[0]
            assert isinstance(block, ToolResultBlock)
            assert block.is_error is True
            assert block.content == "[Cancelled by user]"
        ids = {t.content[0].tool_use_id for t in tombstones}
        assert ids == {"t1", "t2"}

    def test_no_orphans_returns_empty(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            ),
        ]
        tombstones = generate_tombstone_results(msgs, "[Cancelled]")
        assert tombstones == []

    def test_only_unmatched_get_tombstones(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                    ToolUseBlock(id="t2", name="search", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok"),
                ],
            ),
        ]
        tombstones = generate_tombstone_results(msgs, "[Interrupted]")
        assert len(tombstones) == 1
        assert tombstones[0].content[0].tool_use_id == "t2"

    def test_empty_messages_returns_empty(self):
        assert generate_tombstone_results([], "[Cancelled]") == []

    def test_reason_propagated(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="echo", input={}),
                ],
            ),
        ]
        tombstones = generate_tombstone_results(msgs, "[Model fallback: a -> b]")
        assert tombstones[0].content[0].content == "[Model fallback: a -> b]"


class TestCapToolResultWithContentId:
    def test_content_id_embedded_in_truncation(self):
        long_text = "x" * 100_000
        capped = cap_tool_result(long_text, content_id="tr_abc12345")
        assert "tr_abc12345" in capped
        assert "search_content" in capped
        assert "list_content" in capped

    def test_no_content_id_no_persistence_note(self):
        long_text = "x" * 100_000
        capped = cap_tool_result(long_text)
        assert "Persisted as" not in capped
        assert "search_content" not in capped

    def test_content_id_in_head_tail_mode(self):
        head = "a" * 50_000
        tail = "\nTraceback (most recent call last):\nError: boom"
        capped = cap_tool_result(head + tail, content_id="tr_deadbeef")
        assert "tr_deadbeef" in capped
        assert "middle truncated" in capped.lower()

    def test_short_result_ignores_content_id(self):
        short = "ok"
        assert cap_tool_result(short, content_id="tr_12345678") == short


class TestExtractionStub:
    def test_extracts_content_id(self):
        content = "some data\n\n[Result truncated. Persisted as tr_abc12345 — use search_content]"
        stub = extraction_stub(content)
        assert "tr_abc12345" in stub
        assert "search_content" in stub

    def test_no_content_id_returns_trimmed(self):
        assert extraction_stub("plain content") == "[trimmed]"

    def test_extracts_from_middle_truncated(self):
        content = "head\n\n... [middle truncated. Persisted as tr_fedcba98 — use search_content] ...\n\ntail"
        stub = extraction_stub(content)
        assert "tr_fedcba98" in stub


class TestIsCompactedStub:
    def test_trimmed(self):
        assert is_compacted_stub("[trimmed]") is True

    def test_compacted(self):
        assert is_compacted_stub("[compacted]") is True

    def test_retrieval_stub(self):
        assert is_compacted_stub("[Tool result compacted. Persisted as tr_abc — ...]") is True

    def test_normal_content(self):
        assert is_compacted_stub("file contents here") is False


class TestGutToolResultsWithPersistence:
    def test_persisted_result_gets_retrieval_stub(self):
        content = "big data\n\n[Result truncated. Persisted as tr_11111111 — use search_content or list_content.]"
        msg = Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content=content),
            ],
        )
        gutted = gut_tool_results(msg)
        block = gutted.content[0]
        assert "tr_11111111" in block.content
        assert "search_content" in block.content

    def test_unpersisted_result_gets_trimmed(self):
        msg = Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content="plain result data"),
            ],
        )
        gutted = gut_tool_results(msg)
        assert gutted.content[0].content == "[trimmed]"

    def test_string_content_unchanged(self):
        msg = Message(role="user", content="just text")
        assert gut_tool_results(msg) is msg


class TestGuardToolResultsWithPersistence:
    def test_persisted_results_get_retrieval_stubs(self):
        persisted_content = (
            "data " * 10_000
            + "\n\n[Result truncated. Persisted as tr_guard123 — use search_content.]"
        )
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=persisted_content),
                ],
            ),
        ]
        result = guard_tool_results(msgs, context_window=100)
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        assert "tr_guard123" in block.content

    def test_already_compacted_skipped(self):
        msgs = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="[trimmed]"),
                ],
            ),
        ]
        result = guard_tool_results(msgs, context_window=100)
        assert result[0].content[0].content == "[trimmed]"


def tool_pair(tool_use_id: str, tool_name: str, tool_input: dict, result_content: str):
    """Helper: return (assistant_msg, user_msg) for a tool call/result pair."""
    return (
        Message(
            role="assistant",
            content=[
                ToolUseBlock(id=tool_use_id, name=tool_name, input=tool_input),
            ],
        ),
        Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id=tool_use_id, content=result_content),
            ],
        ),
    )


class TestSnipPrepass:
    def test_empty_no_crash(self):
        assert snip_prepass([]) == []

    def test_no_redundancy_unchanged(self):
        a, u = tool_pair("t1", "file_read", {"file_path": "/a.py"}, "content a")
        msgs = [a, u]
        result = snip_prepass(msgs)
        assert result is msgs

    def test_duplicate_reads_keeps_latest(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/a.py"}, "old content")
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/a.py"}, "new content")
        msgs = [a1, u1, a2, u2]
        result = snip_prepass(msgs)
        r1 = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert "[trimmed]" in r1.content or "[Tool result compacted" in r1.content
        r2 = [b for b in result[3].content if isinstance(b, ToolResultBlock)][0]
        assert r2.content == "new content"

    def test_superseded_read_snipped(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/a.py"}, "original")
        a_write = Message(
            role="assistant",
            content=[
                ToolUseBlock(id="tw", name="file_edit", input={"file_path": "/a.py"}),
            ],
        )
        u_write = Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tw", content="edited"),
            ],
        )
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/a.py"}, "after edit")
        msgs = [a1, u1, a_write, u_write, a2, u2]
        result = snip_prepass(msgs)
        r1 = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert r1.content != "original"

    def test_search_then_read_snips_search(self):
        a_grep, u_grep = tool_pair(
            "tg",
            "grep",
            {"pattern": "foo", "path": "/src"},
            "/src/a.py:1:foo\n/src/b.py:3:foo",
        )
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/src/a.py"}, "content a")
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/src/b.py"}, "content b")
        msgs = [a_grep, u_grep, a1, u1, a2, u2]
        result = snip_prepass(msgs)
        rg = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert rg.content != "/src/a.py:1:foo\n/src/b.py:3:foo"

    def test_search_partial_read_not_snipped(self):
        a_grep, u_grep = tool_pair(
            "tg",
            "grep",
            {"pattern": "foo", "path": "/src"},
            "/src/a.py:1:foo\n/src/b.py:3:foo",
        )
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/src/a.py"}, "content a")
        msgs = [a_grep, u_grep, a1, u1]
        result = snip_prepass(msgs)
        rg = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert "foo" in rg.content

    def test_stale_reads_oldest_half(self):
        msgs = []
        a1, u1 = tool_pair("t_old", "file_read", {"file_path": "/old.py"}, "old data")
        msgs.extend([a1, u1])
        for i in range(PRESERVE_RECENT + 2):
            msgs.append(Message(role="user" if i % 2 == 0 else "assistant", content=f"filler {i}"))
        a2, u2 = tool_pair("t_new", "file_read", {"file_path": "/new.py"}, "new data")
        msgs.extend([a2, u2])

        result = snip_prepass(msgs)
        r_old = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert r_old.content != "old data"
        r_new = [b for b in result[-1].content if isinstance(b, ToolResultBlock)][0]
        assert r_new.content == "new data"

    def test_non_snippable_tools_preserved(self):
        msgs = []
        a1, u1 = tool_pair("t_ask", "ask_user", {}, "user response")
        msgs.extend([a1, u1])
        for i in range(PRESERVE_RECENT + 2):
            msgs.append(Message(role="user" if i % 2 == 0 else "assistant", content=f"filler {i}"))
        result = snip_prepass(msgs)
        r = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert r.content == "user response"

    def test_already_compacted_skipped(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/a.py"}, "[trimmed]")
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/a.py"}, "new content")
        msgs = [a1, u1, a2, u2]
        result = snip_prepass(msgs)
        r1 = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert r1.content == "[trimmed]"

    def test_content_id_preserved(self):
        persisted = "data\n\n[Result truncated. Persisted as tr_abc12345 — use search_content.]"
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/a.py"}, persisted)
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/a.py"}, "new content")
        msgs = [a1, u1, a2, u2]
        result = snip_prepass(msgs)
        r1 = [b for b in result[1].content if isinstance(b, ToolResultBlock)][0]
        assert "tr_abc12345" in r1.content

    def test_immutability(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/a.py"}, "old content")
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/a.py"}, "new content")
        msgs = [a1, u1, a2, u2]
        original_content = msgs[1].content[0].content
        snip_prepass(msgs)
        assert msgs[1].content[0].content == original_content

    def test_short_conversation_no_stale(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/a.py"}, "content")
        msgs = [a1, u1]
        result = snip_prepass(msgs)
        assert result is msgs

    def test_search_then_read_ignores_reads_before_search(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/src/a.py"}, "early")
        a_grep, u_grep = tool_pair(
            "tg",
            "grep",
            {"pattern": "foo", "path": "/src"},
            "/src/a.py:1:foo",
        )
        msgs = [a1, u1, a_grep, u_grep]
        result = snip_prepass(msgs)
        rg = [b for b in result[3].content if isinstance(b, ToolResultBlock)][0]
        assert "foo" in rg.content

    def test_search_then_read_with_re_read_after(self):
        a1, u1 = tool_pair("t1", "file_read", {"file_path": "/src/a.py"}, "early")
        a_grep, u_grep = tool_pair(
            "tg",
            "grep",
            {"pattern": "foo", "path": "/src"},
            "/src/a.py:1:foo",
        )
        a2, u2 = tool_pair("t2", "file_read", {"file_path": "/src/a.py"}, "later")
        msgs = [a1, u1, a_grep, u_grep, a2, u2]
        result = snip_prepass(msgs)
        rg = [b for b in result[3].content if isinstance(b, ToolResultBlock)][0]
        assert rg.content != "/src/a.py:1:foo"


class TestCapTurnToolResults:
    def compactable_names(self, *ids):
        return {tid: "bash" for tid in ids}

    def test_under_budget_unchanged(self):
        results = [
            ToolResultBlock(tool_use_id="t1", content="short result"),
            ToolResultBlock(tool_use_id="t2", content="another short"),
        ]
        names = self.compactable_names("t1", "t2")
        capped = cap_turn_tool_results(results, context_window=200_000, tool_names=names)
        assert capped[0].content == "short result"
        assert capped[1].content == "another short"

    def test_over_budget_trims_largest_first(self):
        small = "x" * 1_000
        large = "y" * 200_000
        results = [
            ToolResultBlock(tool_use_id="t1", content=small),
            ToolResultBlock(tool_use_id="t2", content=large),
        ]
        names = self.compactable_names("t1", "t2")
        capped = cap_turn_tool_results(results, context_window=10_000, tool_names=names)
        assert capped[0].content == small
        assert len(capped[1].content) < len(large)
        assert "budget exceeded" in capped[1].content.lower()

    def test_error_results_never_trimmed(self):
        large_error = "Error: " + "x" * 200_000
        results = [
            ToolResultBlock(tool_use_id="t1", content=large_error, is_error=True),
            ToolResultBlock(tool_use_id="t2", content="small"),
        ]
        names = {"t1": "bash", "t2": "bash"}
        capped = cap_turn_tool_results(results, context_window=10_000, tool_names=names)
        assert capped[0].content == large_error

    def test_content_id_preserved_in_trim_note(self):
        content = "data " * 50_000 + "\n\nPersisted as tr_abc12345 — use search_content."
        results = [
            ToolResultBlock(tool_use_id="t1", content=content),
        ]
        names = self.compactable_names("t1")
        capped = cap_turn_tool_results(results, context_window=1_000, tool_names=names)
        assert "tr_abc12345" in capped[0].content

    def test_minimum_chars_preserved(self):
        large = "z" * 200_000
        results = [
            ToolResultBlock(tool_use_id="t1", content=large),
        ]
        names = self.compactable_names("t1")
        capped = cap_turn_tool_results(results, context_window=100, tool_names=names)
        trimmed = capped[0].content
        content_before_note = trimmed.split("\n\n[Aggregate")[0]
        assert len(content_before_note) >= 200

    def test_single_result_under_budget(self):
        result = [ToolResultBlock(tool_use_id="t1", content="ok")]
        names = self.compactable_names("t1")
        assert (
            cap_turn_tool_results(result, context_window=200_000, tool_names=names)[0].content
            == "ok"
        )

    def test_five_parallel_results_over_budget(self):
        results = [ToolResultBlock(tool_use_id=f"t{i}", content="data " * 10_000) for i in range(5)]
        names = {f"t{i}": "read_file" for i in range(5)}
        capped = cap_turn_tool_results(results, context_window=10_000, tool_names=names)
        total_chars = sum(len(r.content) for r in capped)
        original_chars = sum(len(r.content) for r in results)
        assert total_chars < original_chars

    def test_non_compactable_tool_never_trimmed(self):
        large = "y" * 200_000
        results = [
            ToolResultBlock(tool_use_id="t1", content=large),
            ToolResultBlock(tool_use_id="t2", content="small"),
        ]
        names = {"t1": "ask_user", "t2": "bash"}
        capped = cap_turn_tool_results(results, context_window=10_000, tool_names=names)
        assert capped[0].content == large

    def test_mixed_compactable_and_protected(self):
        large_protected = "important " * 50_000
        large_compactable = "data " * 50_000
        results = [
            ToolResultBlock(tool_use_id="t1", content=large_protected),
            ToolResultBlock(tool_use_id="t2", content=large_compactable),
        ]
        names = {"t1": "report_checkpoint", "t2": "read_file"}
        capped = cap_turn_tool_results(results, context_window=10_000, tool_names=names)
        assert capped[0].content == large_protected
        assert len(capped[1].content) < len(large_compactable)

    def test_no_tool_names_trims_all_non_error(self):
        large = "z" * 200_000
        results = [ToolResultBlock(tool_use_id="t1", content=large)]
        capped = cap_turn_tool_results(results, context_window=100)
        assert len(capped[0].content) < len(large)


class TestStripMediaFromMessages:
    def test_no_media_returns_original(self):
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        result = strip_media_from_messages(msgs)
        assert result is msgs

    def test_replaces_image_dict_blocks(self):
        msgs = [
            Message(
                role="user",
                content=[
                    TextBlock(text="look at this"),
                    {"type": "image", "source": {"data": "base64..."}},
                ],
            ),
        ]
        result = strip_media_from_messages(msgs)
        assert len(result[0].content) == 2
        assert isinstance(result[0].content[1], TextBlock)
        assert result[0].content[1].text == "[image]"

    def test_replaces_document_dict_blocks(self):
        msgs = [
            Message(
                role="user",
                content=[
                    {"type": "document", "source": {"data": "pdf..."}},
                ],
            ),
        ]
        result = strip_media_from_messages(msgs)
        assert result[0].content[0].text == "[document]"

    def test_preserves_non_media_blocks(self):
        msgs = [
            Message(
                role="user",
                content=[
                    TextBlock(text="keep this"),
                    ToolResultBlock(tool_use_id="t1", content="keep this too"),
                ],
            ),
        ]
        result = strip_media_from_messages(msgs)
        assert result is msgs

    def test_does_not_mutate_original(self):
        original_block = {"type": "image", "source": {"data": "base64..."}}
        msgs = [
            Message(role="user", content=[original_block]),
        ]
        strip_media_from_messages(msgs)
        assert msgs[0].content[0] is original_block

    def test_strips_image_block_dataclass(self):
        from src.herald.types import ImageBlock

        img = ImageBlock(source={"type": "base64", "media_type": "image/png", "data": "abc"})
        msgs = [
            Message(role="user", content=[TextBlock(text="see"), img]),
        ]
        result = strip_media_from_messages(msgs)
        assert len(result[0].content) == 2
        assert isinstance(result[0].content[1], TextBlock)
        assert result[0].content[1].text == "[image]"

    def test_strips_image_block_nested_in_tool_result(self):
        from src.herald.types import ImageBlock

        img = ImageBlock(source={"type": "base64", "media_type": "image/png", "data": "abc"})
        tr = ToolResultBlock(tool_use_id="t1", content=[img, TextBlock(text="done")])
        msgs = [
            Message(role="user", content=[tr]),
        ]
        result = strip_media_from_messages(msgs)
        inner = result[0].content[0]
        assert isinstance(inner, ToolResultBlock)
        assert isinstance(inner.content[0], TextBlock)
        assert inner.content[0].text == "[image]"
        assert inner.content[1].text == "done"

    def test_preserves_metadata_after_strip(self):
        from src.herald.types import ImageBlock

        img = ImageBlock(source={"type": "base64", "media_type": "image/png", "data": "abc"})
        msgs = [
            Message(role="user", content=[img], metadata={"is_meta": True}),
        ]
        result = strip_media_from_messages(msgs)
        assert result[0].metadata == {"is_meta": True}


class TestFormatCompactSummary:
    def test_strips_analysis_extracts_summary(self):
        raw = "<analysis>thinking...</analysis>\n<summary>The actual summary</summary>"
        result = format_compact_summary(raw)
        assert "Summary:" in result
        assert "The actual summary" in result
        assert "thinking" not in result

    def test_no_summary_tags_returns_text_minus_analysis(self):
        raw = "<analysis>reasoning</analysis>\nJust some plain text"
        result = format_compact_summary(raw)
        assert "reasoning" not in result
        assert "Just some plain text" in result

    def test_no_tags_at_all_returns_raw(self):
        raw = "Plain text summary without any XML tags"
        result = format_compact_summary(raw)
        assert result == raw

    def test_empty_string_returns_empty(self):
        assert format_compact_summary("") == ""

    def test_whitespace_only_returns_original(self):
        raw = "   \n\n  "
        assert format_compact_summary(raw) == raw

    def test_collapses_blank_lines(self):
        raw = "<summary>line1\n\n\n\nline2</summary>"
        result = format_compact_summary(raw)
        assert "\n\n\n" not in result
        assert "line1" in result
        assert "line2" in result

    def test_empty_summary_falls_back(self):
        raw = "<analysis>stuff</analysis>\n<summary></summary>\nFallback text"
        result = format_compact_summary(raw)
        assert "Fallback" in result


class TestExtractTokenGap:
    def test_anthropic_format(self):
        exc = Exception("prompt is too long: 210000 tokens > 200000 maximum")
        gap = extract_token_gap(exc)
        assert gap == 10000

    def test_openai_format(self):
        exc = Exception(
            "This model's maximum context length is 128000 tokens. However, your messages resulted in 150000 tokens."
        )
        gap = extract_token_gap(exc)
        assert gap == 22000

    def test_no_numbers_returns_none(self):
        exc = Exception("something went wrong")
        assert extract_token_gap(exc) is None

    def test_single_number_returns_none(self):
        exc = Exception("too many tokens: 150000")
        assert extract_token_gap(exc) is None

    def test_comma_separated_numbers(self):
        exc = Exception("prompt is too long: 210,000 tokens > 200,000 maximum")
        gap = extract_token_gap(exc)
        assert gap == 10000


class TestDropOldestRounds:
    def make_round(self, user_text, assistant_text):
        return [
            Message(role="user", content=user_text),
            Message(role="assistant", content=assistant_text),
        ]

    def test_drops_20_percent_by_default(self):
        msgs = []
        for i in range(10):
            msgs.extend(self.make_round(f"user {i}", f"assistant {i}"))
        result = drop_oldest_rounds(msgs)
        assert result is not None
        assert len(result) < len(msgs)

    def test_returns_none_for_single_group(self):
        msgs = [Message(role="user", content="only")]
        assert drop_oldest_rounds(msgs) is None

    def test_token_gap_drops_precisely(self):
        msgs = []
        for i in range(5):
            msgs.extend(self.make_round("x" * 1000, "y" * 1000))
        result = drop_oldest_rounds(msgs, token_gap=500)
        assert result is not None
        assert len(result) < len(msgs)

    def test_strips_ptl_marker_from_previous_retry(self):
        marker = Message(role="user", content=PTL_RETRY_MARKER)
        msgs = [marker]
        for i in range(5):
            msgs.extend(self.make_round(f"user {i}", f"assistant {i}"))
        result = drop_oldest_rounds(msgs)
        assert result is not None

    def test_adds_synthetic_marker_for_assistant_first(self):
        msgs = [
            Message(role="assistant", content="response 1"),
            Message(role="user", content="input 1"),
            Message(role="assistant", content="response 2"),
            Message(role="user", content="input 2"),
        ]
        result = drop_oldest_rounds(msgs)
        if result and result[0].role == "user":
            assert result[0].content == PTL_RETRY_MARKER


class TestCompactPrompt:
    def test_contains_no_tools_preamble(self):
        assert "CRITICAL" in COMPACT_PROMPT
        assert "TEXT ONLY" in COMPACT_PROMPT

    def test_contains_all_9_sections(self):
        for section in [
            "Primary Request",
            "Key Technical Concepts",
            "Files and Code",
            "Errors and Fixes",
            "Problem Solving",
            "All User Messages",
            "Pending Tasks",
            "Current Work",
            "Optional Next Step",
        ]:
            assert section in COMPACT_PROMPT

    def test_contains_analysis_tags(self):
        assert "<analysis>" in COMPACT_PROMPT

    def test_contains_summary_tags(self):
        assert "<summary>" in COMPACT_PROMPT

    def test_contains_content_store_note(self):
        assert "search_content" in COMPACT_PROMPT
        assert "list_content" in COMPACT_PROMPT

    def test_contains_no_tools_trailer(self):
        assert COMPACT_PROMPT.rstrip().endswith("you will fail the task.")


class TestBlockContentTextHelper:
    """The block_content_text helper is a private utility but worth
    testing directly because every cap/snip/gut/guard path routes
    through it for the str-vs-list dispatch."""

    def test_string_content_passes_through(self):
        from src.engine_core.context_engineering import block_content_text

        block = ToolResultBlock(tool_use_id="t1", content="hello")
        assert block_content_text(block) == "hello"

    def test_single_text_block_returns_text(self):
        from src.engine_core.context_engineering import block_content_text

        block = ToolResultBlock(
            tool_use_id="t1",
            content=[TextBlock(text="hi")],
        )
        assert block_content_text(block) == "hi"

    def test_multiple_text_blocks_joined_with_blank_line(self):
        from src.engine_core.context_engineering import block_content_text

        block = ToolResultBlock(
            tool_use_id="t1",
            content=[
                TextBlock(text="base"),
                TextBlock(text="[hook]\nattach"),
            ],
        )
        assert block_content_text(block) == "base\n\n[hook]\nattach"

    def test_persisted_block_returns_summary(self):
        from src.engine_core.context_engineering import block_content_text
        from src.herald.types import PersistedBlock

        block = ToolResultBlock(
            tool_use_id="t1",
            content=[PersistedBlock(path="/tmp/r.txt", summary="head excerpt")],
        )
        assert block_content_text(block) == "head excerpt"

    def test_image_blocks_drop_to_empty(self):
        from src.engine_core.context_engineering import block_content_text
        from src.herald.types import ImageBlock

        block = ToolResultBlock(
            tool_use_id="t1",
            content=[
                ImageBlock(source={"type": "base64", "media_type": "image/png", "data": "x"}),
            ],
        )
        assert block_content_text(block) == ""

    def test_mixed_blocks_concatenate_text_only(self):
        from src.engine_core.context_engineering import block_content_text
        from src.herald.types import ImageBlock, PersistedBlock

        block = ToolResultBlock(
            tool_use_id="t1",
            content=[
                TextBlock(text="text"),
                ImageBlock(source={"type": "base64", "media_type": "image/png", "data": "x"}),
                PersistedBlock(path="/p", summary="hint"),
            ],
        )
        out = block_content_text(block)
        assert "text" in out
        assert "hint" in out


class TestCapTurnToolResultsListContent:
    def test_list_content_under_budget_passes_through(self):
        block = ToolResultBlock(
            tool_use_id="t1",
            content=[TextBlock(text="small")],
        )
        out = cap_turn_tool_results(
            [block],
            context_window=200_000,
            tool_names={"t1": "file_read"},
        )
        assert out[0] is block

    def test_list_content_over_budget_collapses_to_string(self):
        big_text = "x" * 100_000
        block = ToolResultBlock(
            tool_use_id="t1",
            content=[TextBlock(text=big_text)],
        )
        out = cap_turn_tool_results(
            [block],
            context_window=200_000,
            tool_names={"t1": "file_read"},
        )
        assert out[0] is not block
        assert isinstance(out[0].content, str)
        assert "[Aggregate tool result budget exceeded" in out[0].content

    def test_mixed_str_and_list_blocks(self):
        big_text = "x" * 100_000
        b1 = ToolResultBlock(tool_use_id="t1", content=big_text)
        b2 = ToolResultBlock(
            tool_use_id="t2",
            content=[TextBlock(text=big_text)],
        )
        out = cap_turn_tool_results(
            [b1, b2],
            context_window=200_000,
            tool_names={"t1": "file_read", "t2": "file_read"},
        )
        assert "[Aggregate tool result budget exceeded" in (
            out[0].content if isinstance(out[0].content, str) else ""
        )
        assert "[Aggregate tool result budget exceeded" in (
            out[1].content if isinstance(out[1].content, str) else ""
        )


class TestGutToolResultsListContent:
    def test_gut_list_content_block(self):
        block = ToolResultBlock(
            tool_use_id="t1",
            content=[TextBlock(text="some big tool output")],
        )
        msg = Message(role="user", content=[block])
        gutted = gut_tool_results(msg)
        assert isinstance(gutted.content, list)
        new_block = gutted.content[0]
        assert isinstance(new_block, ToolResultBlock)
        assert isinstance(new_block.content, str)
        assert "[trimmed]" in new_block.content or "compacted" in new_block.content.lower()


class TestGuardToolResultsListContent:
    def test_guard_handles_list_content_in_budget(self):
        m1 = Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content="legacy"),
                ToolResultBlock(
                    tool_use_id="t2",
                    content=[TextBlock(text="list form")],
                ),
            ],
        )
        out = guard_tool_results([m1], context_window=200_000)
        assert out == [m1]

    def test_guard_compacts_list_content_over_budget(self):
        big_text = "x" * 250_000
        m1 = Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[TextBlock(text=big_text)],
                ),
            ],
        )
        out = guard_tool_results([m1], context_window=200_000)
        new_block = out[0].content[0]
        assert isinstance(new_block, ToolResultBlock)
        assert isinstance(new_block.content, str)
        assert "trimmed" in new_block.content or "compacted" in new_block.content.lower()


class TestEstimateMessagesTokensListContent:
    def test_list_content_block_counted(self):
        from src.engine_core.context_engineering import estimate_messages_tokens

        msg = Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[TextBlock(text="x" * 1000)],
                ),
            ],
        )
        tokens = estimate_messages_tokens([msg])
        assert 400 <= tokens <= 800

    def test_str_and_list_give_same_token_count(self):
        from src.engine_core.context_engineering import estimate_messages_tokens

        text = "x" * 1000
        msg_str = Message(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content=text),
            ],
        )
        msg_list = Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="t1",
                    content=[TextBlock(text=text)],
                ),
            ],
        )
        assert estimate_messages_tokens([msg_str]) == estimate_messages_tokens([msg_list])


def make_bulky_messages(rounds: int = 20, chars_per_msg: int = 4000):
    """Build alternating user/assistant message rounds with padded content."""
    msgs = []
    for i in range(rounds):
        msgs.append(
            Message(role="user", content=f"u{i} " + "x" * chars_per_msg, timestamp=float(i))
        )
        msgs.append(
            Message(
                role="assistant", content=f"a{i} " + "y" * chars_per_msg, timestamp=float(i) + 0.5
            )
        )
    return msgs


class TestPtlRetryFeatureFlag:
    """Verify the FEATURE_COMPACTION_PTL_RETRY flag gates retry behaviour.

    These tests use the pre-check path (token estimate exceeds context_window
    before any LLM call). The function needs an adapter argument but the
    pre-check fires before the adapter is called, so we pass a dummy that
    would raise if called — proving the pre-check path runs.
    """

    @pytest.mark.asyncio
    async def test_flag_disabled_no_retry(self, monkeypatch):
        """When FEATURE_COMPACTION_PTL_RETRY=false, overflow returns empty immediately."""
        from src.engine_core.context_engineering import summarize_old_messages, PRESERVE_RECENT

        monkeypatch.setenv("FEATURE_COMPACTION_PTL_RETRY", "false")

        msgs = make_bulky_messages(rounds=20, chars_per_msg=4000)

        events = []

        async def capture(evt):
            events.append(evt)

        summary, recent = await summarize_old_messages(
            msgs,
            adapter=None,
            preserve_recent=PRESERVE_RECENT,
            context_window=5000,
            on_ptl_event=capture,
        )

        assert summary == ""
        assert len(recent) == PRESERVE_RECENT
        from src.engine_core.events import CompactionPtlRetryEvent

        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        assert len(retry_events) == 0, "No retry events should fire when flag is disabled"

    @pytest.mark.asyncio
    async def test_flag_enabled_triggers_retry(self, monkeypatch):
        """When flag is enabled (default), pre-check overflow triggers retry events."""
        from src.engine_core.context_engineering import summarize_old_messages, PRESERVE_RECENT

        monkeypatch.setenv("FEATURE_COMPACTION_PTL_RETRY", "true")

        msgs = make_bulky_messages(rounds=20, chars_per_msg=4000)

        events = []

        async def capture(evt):
            events.append(evt)

        summary, recent = await summarize_old_messages(
            msgs,
            adapter=None,
            preserve_recent=PRESERVE_RECENT,
            context_window=5000,
            on_ptl_event=capture,
        )

        assert summary == ""
        from src.engine_core.events import CompactionPtlRetryEvent

        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        assert len(retry_events) >= 1, "At least one retry event should fire"
        assert retry_events[0].trigger == "pre_check"


class TestPtlRetryTelemetry:
    """Verify structured events are emitted at the right points."""

    @pytest.mark.asyncio
    async def test_precheck_emits_retry_event(self):
        from src.engine_core.context_engineering import summarize_old_messages, PRESERVE_RECENT
        from src.engine_core.events import CompactionPtlRetryEvent

        msgs = make_bulky_messages(rounds=20, chars_per_msg=4000)

        events = []

        async def capture(evt):
            events.append(evt)

        await summarize_old_messages(
            msgs,
            adapter=None,
            preserve_recent=PRESERVE_RECENT,
            context_window=5000,
            on_ptl_event=capture,
        )

        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        assert len(retry_events) >= 1
        first = retry_events[0]
        assert first.trigger == "pre_check"
        assert first.attempt == 1
        assert first.token_gap is not None
        assert first.token_gap > 0
        assert first.messages_after < first.messages_before

    @pytest.mark.asyncio
    async def test_exhaustion_emits_failed_event(self):
        """Messages so large that even 3 retries can't fit — verify failure event."""
        from src.engine_core.context_engineering import summarize_old_messages
        from src.engine_core.events import CompactionPtlFailedEvent

        msgs = make_bulky_messages(rounds=50, chars_per_msg=8000)

        events = []

        async def capture(evt):
            events.append(evt)

        summary, _ = await summarize_old_messages(
            msgs,
            adapter=None,
            preserve_recent=5,
            context_window=500,
            on_ptl_event=capture,
        )

        assert summary == ""
        failed_events = [e for e in events if isinstance(e, CompactionPtlFailedEvent)]
        assert len(failed_events) >= 1
        last_fail = failed_events[-1]
        assert last_fail.reason in ("retries_exhausted", "context_exceeded", "no_droppable_rounds")

    @pytest.mark.asyncio
    async def test_retry_event_fields_populated(self):
        from src.engine_core.context_engineering import summarize_old_messages, PRESERVE_RECENT
        from src.engine_core.events import CompactionPtlRetryEvent

        msgs = make_bulky_messages(rounds=15, chars_per_msg=3000)

        events = []

        async def capture(evt):
            events.append(evt)

        await summarize_old_messages(
            msgs,
            adapter=None,
            preserve_recent=PRESERVE_RECENT,
            context_window=8000,
            on_ptl_event=capture,
        )

        retry_events = [e for e in events if isinstance(e, CompactionPtlRetryEvent)]
        if retry_events:
            evt = retry_events[0]
            assert evt.thread_id == ""
            assert evt.max_retries == 3
            assert evt.messages_before > 0
            assert evt.messages_after > 0


class TestGuardToolResultsRoundDrop:
    def test_round_drop_when_per_block_insufficient(self):
        big = "x" * 250_000
        msgs = [
            Message(role="user", content="start"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=big),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="read", input={}),
                ],
                api_message_id="msg_002",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content=big),
                ],
            ),
            Message(role="assistant", content="done", api_message_id="msg_003"),
        ]
        result = guard_tool_results(msgs, context_window=200)
        for msg in result:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        assert len(block.content) < len(big)

    def test_round_drop_fires_when_stubs_still_over_budget(self):
        big = "x" * 250_000
        msgs = [
            Message(role="user", content="start"),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=big),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="read", input={}),
                ],
                api_message_id="msg_002",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content=big),
                ],
            ),
            Message(role="assistant", content="done", api_message_id="msg_003"),
        ]
        result = guard_tool_results(msgs, context_window=4)
        assert len(result) < len(msgs)

    def test_preserves_api_message_id_on_copy(self):
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
                api_message_id="msg_001",
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="data " * 50_000),
                ],
            ),
        ]
        result = guard_tool_results(msgs, context_window=100)
        assert result[0].api_message_id == "msg_001"


class TestContentReplacementState:
    def test_cached_replacement_reused(self):
        from src.engine_core.context_engineering import ContentReplacementState

        state = ContentReplacementState()
        big = "x" * 250_000
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=big),
                ],
            ),
        ]
        result1 = guard_tool_results(msgs, context_window=100, replacement_state=state)
        stub1 = result1[1].content[0].content

        result2 = guard_tool_results(msgs, context_window=100, replacement_state=state)
        stub2 = result2[1].content[0].content

        assert stub1 == stub2
        assert state.replacements.get("t1") == stub1

    def test_frozen_result_never_replaced(self):
        from src.engine_core.context_engineering import ContentReplacementState

        state = ContentReplacementState()
        small = "y" * 100
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=small),
                ],
            ),
        ]
        guard_tool_results(msgs, context_window=200_000, replacement_state=state)
        assert state.is_frozen("t1")
        assert state.get_cached_replacement("t1") is None

        big = "z" * 250_000
        msgs2 = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=big),
                ],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t2", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t2", content=big),
                ],
            ),
        ]
        result = guard_tool_results(msgs2, context_window=100, replacement_state=state)
        t1_block = result[1].content[0]
        assert t1_block.content == big

    def test_feature_flag_false_skips_caching(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHE_STABLE_REPLACEMENTS", "false")
        from src.engine_core.context_engineering import ContentReplacementState

        state = ContentReplacementState()
        big = "x" * 250_000
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=big),
                ],
            ),
        ]
        guard_tool_results(msgs, context_window=100, replacement_state=state)
        assert len(state.seen_ids) == 0
        assert len(state.replacements) == 0

    def test_none_state_backward_compatible(self):
        big = "x" * 250_000
        msgs = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="t1", name="read", input={}),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content=big),
                ],
            ),
        ]
        result = guard_tool_results(msgs, context_window=100, replacement_state=None)
        assert result[1].content[0].content != big


class TestDropOldestRoundsWithIds:
    def test_drops_with_message_ids(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1 " * 200, api_message_id="msg_001"),
            Message(role="user", content="q2"),
            Message(role="assistant", content="a2 " * 200, api_message_id="msg_002"),
            Message(role="user", content="q3"),
            Message(role="assistant", content="a3", api_message_id="msg_003"),
        ]
        result = drop_oldest_rounds(msgs, token_gap=500)
        assert result is not None
        assert len(result) < len(msgs)
        assert result[-1].content == "a3"
