"""Tests for cache-aware snip prepass (src/engine_core/cached_snip.py).

Verifies:
  - snip_prepass_get_deletes identifies the same IDs as snip_prepass stubs
  - CachedSnipState state management (queue, consume, pin, reset)
  - apply_cache_edits_to_messages: cache_reference annotation + cache_edits insertion
  - Deduplication of pinned edits across turns
  - header_latched is preserved across reset_state (latch-on, never reset)
  - insert_after_tool_results placement rules
"""

from __future__ import annotations


from src.engine_core.cached_snip import (
    CACHE_EDITING_BETA_HEADER,
    CachedSnipState,
    PinnedCacheEdit,
    apply_cache_edits_to_messages,
    cached_snip_prepass_enabled,
    consume_pending_deletes,
    detect_unexpected_cache_break,
    is_model_supported_for_cache_editing,
    pin_edits,
    queue_deletions,
    reset_state,
    snip_prepass_get_deletes,
    insert_after_tool_results,
)
from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock


# ── Helpers ───────────────────────────────────────────────────────────────────


def msg(role: str, content) -> Message:
    if isinstance(content, str):
        return Message(role=role, content=content)
    return Message(role=role, content=content)


def read_pair(tool_id: str, path: str, result: str) -> list[Message]:
    """Return (assistant with tool_use, user with tool_result) pair for a file read."""
    use = Message(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name="file_read", input={"file_path": path})],
    )
    result_msg = Message(
        role="user",
        content=[ToolResultBlock(tool_use_id=tool_id, content=result)],
    )
    return [use, result_msg]


def serialized_messages(messages: list[Message]) -> list[dict]:
    """Minimal serialization to dicts for apply_cache_edits_to_messages."""
    from src.herald.provider import to_wire_messages

    return to_wire_messages(messages)


def place_cc(messages: list[dict]) -> None:
    """Place cache_control on the last content block of the last message."""
    from src.herald.provider import place_message_cache_marker

    place_message_cache_marker(messages)


# ── Feature flag ──────────────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("FEATURE_CACHED_SNIP_PREPASS", raising=False)
        assert cached_snip_prepass_enabled() is False

    def test_enabled_when_set(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "true")
        assert cached_snip_prepass_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "TRUE")
        assert cached_snip_prepass_enabled() is True


# ── State management ─────────────────────────────────────────────────────────


class TestCachedSnipState:
    def test_queue_and_consume(self):
        state = CachedSnipState()
        queue_deletions(state, ["id1", "id2"])
        result = consume_pending_deletes(state)
        assert set(result) == {"id1", "id2"}
        # Pending is cleared after consume
        assert consume_pending_deletes(state) is None

    def test_queue_empty_is_noop(self):
        state = CachedSnipState()
        queue_deletions(state, [])
        assert consume_pending_deletes(state) is None

    def test_pin_edits(self):
        state = CachedSnipState()
        pin_edits(state, 3, ["id1", "id2"])
        assert len(state.pinned_edits) == 1
        assert state.pinned_edits[0].user_message_index == 3
        assert state.pinned_edits[0].tool_ids == ["id1", "id2"]

    def test_reset_clears_pending_and_pinned(self):
        state = CachedSnipState()
        queue_deletions(state, ["id1"])
        pin_edits(state, 0, ["id2"])
        reset_state(state)
        assert state.pending_deletes == []
        assert state.pinned_edits == []

    def test_reset_preserves_header_latched(self):
        state = CachedSnipState()
        state.header_latched = True
        queue_deletions(state, ["id1"])
        reset_state(state)
        # header_latched must survive reset — once committed, always send.
        assert state.header_latched is True

    def test_multiple_queues_accumulate(self):
        state = CachedSnipState()
        queue_deletions(state, ["id1"])
        queue_deletions(state, ["id2", "id3"])
        result = consume_pending_deletes(state)
        assert set(result) == {"id1", "id2", "id3"}


# ── snip_prepass_get_deletes ──────────────────────────────────────────────────


class TestSnipPrepassGetDeletes:
    def test_returns_empty_for_short_conversation(self):
        messages = read_pair("t1", "/a.py", "content")
        assert snip_prepass_get_deletes(messages) == []

    def test_identifies_duplicate_reads(self):
        # Same file read twice → first read is redundant
        msgs = (
            read_pair("t1", "/file.py", "v1")
            + [Message(role="assistant", content="ok")]  # padding
            + read_pair("t2", "/file.py", "v2")
        )
        ids = snip_prepass_get_deletes(msgs)
        assert "t1" in ids
        assert "t2" not in ids

    def test_no_false_positives_on_unique_reads(self):
        msgs = (
            read_pair("t1", "/a.py", "content_a")
            + [Message(role="assistant", content="ok")]
            + read_pair("t2", "/b.py", "content_b")
        )
        ids = snip_prepass_get_deletes(msgs)
        assert ids == []

    def test_does_not_mutate_messages(self):
        msgs = (
            read_pair("t1", "/file.py", "v1")
            + [Message(role="assistant", content="ok")]
            + read_pair("t2", "/file.py", "v2")
        )
        original_contents = [
            getattr(b, "content", None)
            for m in msgs
            for b in (m.content if isinstance(m.content, list) else [])
        ]
        snip_prepass_get_deletes(msgs)
        after_contents = [
            getattr(b, "content", None)
            for m in msgs
            for b in (m.content if isinstance(m.content, list) else [])
        ]
        assert original_contents == after_contents


# ── insert_after_tool_results ────────────────────────────────────────────────


class TestInsertAfterToolResults:
    def test_inserts_after_last_tool_result(self):
        content = [
            {"type": "tool_result", "tool_use_id": "a"},
            {"type": "tool_result", "tool_use_id": "b"},
            {"type": "text", "text": "some text"},
        ]
        block = {"type": "cache_edits", "edits": []}
        result = insert_after_tool_results(content, block)
        # Should be inserted at index 2 (after last tool_result at index 1)
        assert result[2] == block
        assert result[0]["type"] == "tool_result"
        assert result[1]["type"] == "tool_result"
        assert result[3]["type"] == "text"

    def test_inserts_at_end_when_no_tool_results(self):
        content = [{"type": "text", "text": "hello"}]
        block = {"type": "cache_edits", "edits": []}
        result = insert_after_tool_results(content, block)
        # With no tool_results, inserts before the last block
        assert block in result

    def test_appends_text_sentinel_when_edits_block_would_be_last(self):
        # Only tool_results, no trailing text → sentinel "." should be appended
        content = [{"type": "tool_result", "tool_use_id": "x"}]
        block = {"type": "cache_edits", "edits": []}
        result = insert_after_tool_results(content, block)
        # cache_edits inserted at index 1; since it would be last, a text sentinel follows
        assert result[1] == block
        assert result[2] == {"type": "text", "text": "."}

    def test_returns_new_list(self):
        content = [{"type": "tool_result", "tool_use_id": "x"}]
        result = insert_after_tool_results(content, {"type": "cache_edits", "edits": []})
        assert result is not content


# ── apply_cache_edits_to_messages ─────────────────────────────────────────────


def make_api_messages_with_cc() -> list[dict]:
    """
    Build a minimal serialized message array with a cache_control marker:
      [user(tr:id1), asst(text), user(tr:id2, text+cache_control)]
    The last cache_control is on the last user message. id1 and id2 are in
    the user messages; only id1 is before the last cc message.
    """
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "id1", "content": "data"},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "reply"}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "id2", "content": "data2"},
                {"type": "text", "text": "user text", "cache_control": {"type": "ephemeral"}},
            ],
        },
    ]
    return msgs


class TestApplyCacheEditsToMessages:
    def test_adds_cache_reference_to_blocks_before_last_cc(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        result, any_edits = apply_cache_edits_to_messages(msgs, state, None)
        # id1 is in msg[0], which is BEFORE msg[2] (last cc) → gets cache_reference
        user0_blocks = result[0]["content"]
        tr_block = next(b for b in user0_blocks if b.get("type") == "tool_result")
        assert tr_block.get("cache_reference") == "id1"

    def test_does_not_add_cache_reference_at_last_cc_message(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        result, _ = apply_cache_edits_to_messages(msgs, state, None)
        # id2 is in msg[2] (the last cc message itself) → must NOT get cache_reference
        user2_blocks = result[2]["content"]
        tr_block = next(b for b in user2_blocks if b.get("type") == "tool_result")
        assert "cache_reference" not in tr_block

    def test_inserts_cache_edits_block_with_new_deletes(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        result, any_edits = apply_cache_edits_to_messages(msgs, state, ["id1"])
        assert any_edits is True
        # cache_edits should be in the last user message (msg[2])
        user2_blocks = result[2]["content"]
        edits_blocks = [b for b in user2_blocks if b.get("type") == "cache_edits"]
        assert len(edits_blocks) == 1
        edits = edits_blocks[0]["edits"]
        assert len(edits) == 1
        assert edits[0] == {"type": "delete", "cache_reference": "id1"}

    def test_pins_new_edits_to_state(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        apply_cache_edits_to_messages(msgs, state, ["id1"])
        assert len(state.pinned_edits) == 1
        assert state.pinned_edits[0].tool_ids == ["id1"]

    def test_re_inserts_pinned_edits(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        # Pre-populate a pinned edit at position 0 referencing id1 (present
        # in msgs). Ids absent from current messages are dropped by the
        # cross-context pollution guard — tested separately.
        state.pinned_edits.append(PinnedCacheEdit(user_message_index=0, tool_ids=["id1"]))
        result, any_edits = apply_cache_edits_to_messages(msgs, state, None)
        assert any_edits is True
        user0_blocks = result[0]["content"]
        edits_blocks = [b for b in user0_blocks if b.get("type") == "cache_edits"]
        assert len(edits_blocks) == 1
        assert edits_blocks[0]["edits"][0]["cache_reference"] == "id1"

    def test_deduplicates_across_pinned_and_new(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        # Pin id1 at position 0
        state.pinned_edits.append(PinnedCacheEdit(user_message_index=0, tool_ids=["id1"]))
        # Also try to delete id1 as "new" — should be deduped
        result, any_edits = apply_cache_edits_to_messages(msgs, state, ["id1"])
        # id1 should appear exactly once across all cache_edits blocks
        all_edits_blocks = [
            b
            for msg in result
            for b in (msg.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "cache_edits"
        ]
        all_refs = [
            e["cache_reference"] for block in all_edits_blocks for e in block.get("edits", [])
        ]
        assert all_refs.count("id1") == 1

    def test_returns_false_when_nothing_to_do(self):
        msgs = make_api_messages_with_cc()
        state = CachedSnipState()
        _, any_edits = apply_cache_edits_to_messages(msgs, state, None)
        assert any_edits is False

    def test_empty_messages_is_noop(self):
        state = CachedSnipState()
        result, any_edits = apply_cache_edits_to_messages([], state, ["id1"])
        assert result == []
        assert any_edits is False


# ── Provider integration (Provider) ─────────────────────────────────


class TestProviderCachedSnip:
    """Integration tests verifying that Provider.build_kwargs wires
    the cached snip state correctly."""

    def make_provider(self, model: str = "claude-sonnet-4-6"):
        from unittest.mock import MagicMock
        from src.herald.provider import Provider
        from src.config import Settings

        settings = MagicMock(spec=Settings)
        settings.cdt_url = "http://citadel-test"
        settings.cdt_key = "sk-sept-cit-test-token"
        settings.engine_id = ""
        settings.llm_model = model
        settings.max_tokens = 1000
        provider = Provider(settings)
        return provider

    def test_queue_snip_cache_edits_initializes_state(self):
        p = self.make_provider()
        assert p.cached_snip_state is None
        p.queue_snip_cache_edits(["id1", "id2"])
        assert p.cached_snip_state is not None
        assert set(p.cached_snip_state.pending_deletes) == {"id1", "id2"}

    def test_reset_snip_state_clears_pending_and_pinned(self):
        p = self.make_provider()
        p.queue_snip_cache_edits(["id1"])
        p.cached_snip_state.pinned_edits.append(
            PinnedCacheEdit(user_message_index=0, tool_ids=["id2"])
        )
        p.reset_snip_state()
        assert p.cached_snip_state.pending_deletes == []
        assert p.cached_snip_state.pinned_edits == []

    def test_reset_snip_state_preserves_header_latched(self):
        p = self.make_provider()
        p.queue_snip_cache_edits(["id1"])
        p.cached_snip_state.header_latched = True
        p.reset_snip_state()
        assert p.cached_snip_state.header_latched is True

    def test_build_kwargs_injects_cache_edits_when_enabled(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "true")
        p = self.make_provider()
        p.queue_snip_cache_edits(["tool_id_123"])
        # Force state so build_kwargs will use it
        p.cached_snip_state.header_latched = False

        from src.herald.types import Message, ToolUseBlock, ToolResultBlock

        # Build a minimal message array: assistant tool use + user tool result
        messages = [
            Message(
                role="user",
                content=[TextBlock(text="Hello")],
            ),
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(id="tool_id_123", name="file_read", input={"file_path": "/x"})
                ],
            ),
            Message(
                role="user",
                content=[ToolResultBlock(tool_use_id="tool_id_123", content="file data")],
            ),
        ]

        kwargs = p.build_kwargs(messages, None, None)
        api_msgs = kwargs["messages"]

        # Find the cache_edits block in the last user message
        last_user_msg = next((m for m in reversed(api_msgs) if m.get("role") == "user"), None)
        assert last_user_msg is not None
        edits_blocks = [
            b
            for b in last_user_msg["content"]
            if isinstance(b, dict) and b.get("type") == "cache_edits"
        ]
        assert len(edits_blocks) == 1
        edits = edits_blocks[0]["edits"]
        assert any(e["cache_reference"] == "tool_id_123" for e in edits)

    def test_build_kwargs_adds_beta_header_when_editing(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "true")
        p = self.make_provider()
        p.queue_snip_cache_edits(["id1"])

        from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock

        messages = [
            Message(role="user", content=[TextBlock(text="start")]),
            Message(role="assistant", content=[ToolUseBlock(id="id1", name="file_read", input={})]),
            Message(role="user", content=[ToolResultBlock(tool_use_id="id1", content="data")]),
        ]
        kwargs = p.build_kwargs(messages, None, None)
        assert "betas" in kwargs
        assert CACHE_EDITING_BETA_HEADER in kwargs["betas"]

    def test_build_kwargs_no_beta_when_nothing_pending(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "true")
        p = self.make_provider()
        # No pending deletions, no latched header → no betas should be added
        from src.herald.types import Message, TextBlock

        messages = [Message(role="user", content=[TextBlock(text="hello")])]
        kwargs = p.build_kwargs(messages, None, None)
        assert "betas" not in kwargs or kwargs.get("betas") == []

    def test_build_kwargs_keeps_beta_when_header_latched(self, monkeypatch):
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "true")
        p = self.make_provider()
        p.queue_snip_cache_edits([])  # initializes state
        p.cached_snip_state.header_latched = True  # simulates prior send

        from src.herald.types import Message, TextBlock

        messages = [Message(role="user", content=[TextBlock(text="hi")])]
        kwargs = p.build_kwargs(messages, None, None)
        # Header must still be sent even with no new edits
        assert "betas" in kwargs
        assert CACHE_EDITING_BETA_HEADER in kwargs["betas"]

    def test_build_kwargs_skips_injection_on_unsupported_model(self, monkeypatch):
        """Model gate: unsupported models must not get cache_edits injected."""
        monkeypatch.setenv("FEATURE_CACHED_SNIP_PREPASS", "true")
        p = self.make_provider(model="claude-3-5-sonnet-20241022")  # unsupported
        p.queue_snip_cache_edits(["tool_id_123"])

        from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock

        messages = [
            Message(role="user", content=[TextBlock(text="Hello")]),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="tool_id_123", name="file_read", input={})],
            ),
            Message(
                role="user", content=[ToolResultBlock(tool_use_id="tool_id_123", content="data")]
            ),
        ]
        kwargs = p.build_kwargs(messages, None, None)
        api_msgs = kwargs["messages"]
        last_user = next((m for m in reversed(api_msgs) if m.get("role") == "user"), None)
        edits_blocks = [
            b
            for b in last_user["content"]
            if isinstance(b, dict) and b.get("type") == "cache_edits"
        ]
        assert edits_blocks == []
        assert "betas" not in kwargs or kwargs.get("betas") == []


# ── Model support gate ───────────────────────────────────────────────────────


class TestModelSupport:
    def test_supported_models(self):
        assert is_model_supported_for_cache_editing("claude-opus-4-6")
        assert is_model_supported_for_cache_editing("claude-sonnet-4-6")
        assert is_model_supported_for_cache_editing("claude-haiku-4-5")

    def test_unsupported_models(self):
        assert not is_model_supported_for_cache_editing("claude-3-5-sonnet-20241022")
        assert not is_model_supported_for_cache_editing("gpt-5.4")
        assert not is_model_supported_for_cache_editing("")
        assert not is_model_supported_for_cache_editing(None)


# ── Cache-break detection ────────────────────────────────────────────────────


class TestCacheBreakDetection:
    def test_no_state_no_break(self):
        assert detect_unexpected_cache_break(None, 0, 100) is False

    def test_empty_state_no_break(self):
        state = CachedSnipState()
        assert detect_unexpected_cache_break(state, 0, 100) is False

    def test_cold_prev_no_break(self):
        state = CachedSnipState()
        state.pinned_edits.append(PinnedCacheEdit(user_message_index=0, tool_ids=["a"]))
        # prev was already cold — first turn or post-reset, nothing to lose
        assert detect_unexpected_cache_break(state, 0, 0) is False

    def test_cache_still_warm_no_break(self):
        state = CachedSnipState()
        state.pinned_edits.append(PinnedCacheEdit(user_message_index=0, tool_ids=["a"]))
        # cache_read > 0 — server still knows our references
        assert detect_unexpected_cache_break(state, 500, 1000) is False

    def test_unexpected_break_detected(self):
        state = CachedSnipState()
        state.pinned_edits.append(PinnedCacheEdit(user_message_index=0, tool_ids=["a"]))
        # Prior turn warm, current turn cache gone — our pinned refs are stale
        assert detect_unexpected_cache_break(state, 0, 1000) is True

    def test_pending_only_triggers_break(self):
        """Pending-but-not-yet-pinned state is also at risk."""
        state = CachedSnipState()
        state.pending_deletes.append("id1")
        assert detect_unexpected_cache_break(state, 0, 1000) is True


class TestAdapterObservesCacheBreak:
    def make_provider(self, model: str = "claude-sonnet-4-6"):
        from unittest.mock import MagicMock
        from src.herald.provider import Provider
        from src.config import Settings

        settings = MagicMock(spec=Settings)
        settings.cdt_url = "http://citadel-test"
        settings.cdt_key = "sk-sept-cit-test-token"
        settings.engine_id = ""
        settings.llm_model = model
        settings.max_tokens = 1000
        return Provider(settings)

    def test_first_observation_sets_prev(self):
        p = self.make_provider()
        assert p.prev_cache_read == 0
        p.observe_usage_for_cache_break(1000)
        assert p.prev_cache_read == 1000

    def test_break_resets_state(self):
        p = self.make_provider()
        p.queue_snip_cache_edits(["id1"])
        p.cached_snip_state.pinned_edits.append(
            PinnedCacheEdit(user_message_index=0, tool_ids=["id1"])
        )
        p.cached_snip_state.header_latched = True
        # Warm turn
        p.observe_usage_for_cache_break(1000)
        assert p.cached_snip_state.pinned_edits  # still there
        # Cold turn — break detected
        p.observe_usage_for_cache_break(0)
        assert p.cached_snip_state.pending_deletes == []
        assert p.cached_snip_state.pinned_edits == []
        # header_latched survives — we keep sending header for session
        assert p.cached_snip_state.header_latched is True
        # prev updated even on break
        assert p.prev_cache_read == 0

    def test_warm_turn_preserves_state(self):
        p = self.make_provider()
        p.queue_snip_cache_edits(["id1"])
        p.cached_snip_state.pinned_edits.append(
            PinnedCacheEdit(user_message_index=0, tool_ids=["id1"])
        )
        p.observe_usage_for_cache_break(500)
        p.observe_usage_for_cache_break(600)
        assert p.cached_snip_state.pinned_edits  # untouched


# ── Cross-context pollution guard (shared adapter / sub-agent) ──────────────


class TestApplyCacheEditsFiltering:
    def serialized(self, messages: list) -> list[dict]:
        from src.herald.provider import to_wire_messages

        return to_wire_messages(messages)

    def test_pinned_edits_filtered_to_present_ids(self):
        """Pinned edits referencing tool_ids NOT in the current messages must
        be dropped — defends against shared-adapter sub-agent contexts."""
        from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock

        state = CachedSnipState()
        # Pinned edit references "parent_tool_id" (from parent thread)
        state.pinned_edits.append(
            PinnedCacheEdit(user_message_index=0, tool_ids=["parent_tool_id"])
        )

        # Sub-agent messages contain only "subagent_tool_id"
        messages = [
            Message(role="user", content=[TextBlock(text="sub task")]),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="subagent_tool_id", name="file_read", input={})],
            ),
            Message(
                role="user",
                content=[ToolResultBlock(tool_use_id="subagent_tool_id", content="data")],
            ),
        ]
        api_msgs = self.serialized(messages)
        result, any_edits = apply_cache_edits_to_messages(api_msgs, state, None)

        # No cache_edits block should have been injected with parent_tool_id
        for msg in result:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "cache_edits":
                    for edit in block.get("edits", []):
                        assert edit.get("cache_reference") != "parent_tool_id"

    def test_new_deletes_filtered_to_present_ids(self):
        """Queued new deletes for absent tool_ids must be dropped."""
        from src.herald.types import Message, TextBlock, ToolUseBlock, ToolResultBlock

        state = CachedSnipState()
        messages = [
            Message(role="user", content=[TextBlock(text="hi")]),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="present_id", name="file_read", input={})],
            ),
            Message(role="user", content=[ToolResultBlock(tool_use_id="present_id", content="x")]),
        ]
        api_msgs = self.serialized(messages)
        result, any_edits = apply_cache_edits_to_messages(
            api_msgs, state, ["absent_id", "present_id"]
        )

        # Only "present_id" should end up in the cache_edits block
        found_refs: list[str] = []
        for msg in result:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "cache_edits":
                    for edit in block.get("edits", []):
                        found_refs.append(edit.get("cache_reference"))
        assert "absent_id" not in found_refs
        assert "present_id" in found_refs
