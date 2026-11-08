"""Tests for prompt cache-aware system prompt sections.

Covers: section registration, resolution, memoization, boundary split,
cache scope assignment, ConsciousnessManager integration, Anthropic
multi-block formatting, message-level cache markers, tool schema session
cache, beta header latching, cache break detection, and false-positive
suppression.
"""

from __future__ import annotations

import hashlib
import pytest

from src.herald.types import (
    Message,
    SystemPromptBlock,
    ToolDef,
    Usage,
)
from src.memory.consciousness import (
    ConsciousnessManager,
    SectionStability,
    DYNAMIC_BOUNDARY,
)
from src.engine_core.prompt_cache_break_detection import (
    PromptState,
    CacheBreakType,
    record_prompt_state,
    check_response_for_cache_break,
    notify_cache_deletion,
    consume_pending_cache_deletion,
    is_cache_deletion_pending,
    reset_pending_deletions,
)


@pytest.fixture
def mgr():
    """ConsciousnessManager without DB dependencies — sections-only testing."""
    return ConsciousnessManager()


@pytest.fixture(autouse=True)
def reset_cache_detection():
    """Reset global cache deletion tracking between tests."""
    reset_pending_deletions()
    yield
    reset_pending_deletions()


class TestSectionRegistration:
    def test_register_section(self, mgr):
        mgr.register_section("test", SectionStability.STABLE, lambda: "hello")
        assert len(mgr.sections) == 1
        assert mgr.sections[0][0] == "test"

    def test_resolve_sections_returns_content(self, mgr):
        mgr.register_section("a", SectionStability.STABLE, lambda: "alpha")
        mgr.register_section("b", SectionStability.DYNAMIC, lambda: "beta")
        result = mgr.resolve_sections()
        assert len(result) == 2
        assert result[0] == ("a", SectionStability.STABLE, "alpha")
        assert result[1] == ("b", SectionStability.DYNAMIC, "beta")

    def test_resolve_preserves_registration_order(self, mgr):
        mgr.register_section("z", SectionStability.STABLE, lambda: "last")
        mgr.register_section("a", SectionStability.STABLE, lambda: "first")
        result = mgr.resolve_sections()
        assert result[0][0] == "z"
        assert result[1][0] == "a"


class TestSectionMemoization:
    def test_stable_section_memoized(self, mgr):
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return "stable-content"

        mgr.register_section("s", SectionStability.STABLE, compute)
        mgr.resolve_sections()
        mgr.resolve_sections()
        mgr.resolve_sections()
        assert call_count == 1

    def test_dynamic_section_memoized(self, mgr):
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return "dynamic-content"

        mgr.register_section("d", SectionStability.DYNAMIC, compute)
        mgr.resolve_sections()
        mgr.resolve_sections()
        assert call_count == 1

    def test_volatile_section_recomputes_every_call(self, mgr):
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return f"volatile-{call_count}"

        mgr.register_section("v", SectionStability.VOLATILE, compute)
        mgr.resolve_sections()
        mgr.resolve_sections()
        mgr.resolve_sections()
        assert call_count == 3

    def test_clear_section_cache_forces_recompute(self, mgr):
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return "content"

        mgr.register_section("s", SectionStability.STABLE, compute)
        mgr.resolve_sections()
        assert call_count == 1
        mgr.clear_section_cache()
        mgr.resolve_sections()
        assert call_count == 2

    def test_invalidate_clears_sections(self, mgr):
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return "content"

        mgr.register_section("s", SectionStability.STABLE, compute)
        mgr.resolve_sections()
        assert call_count == 1
        mgr.invalidate()
        mgr.resolve_sections()
        assert call_count == 2


class TestBoundaryAndScopes:
    def test_stable_before_boundary_gets_global_scope(self, mgr):
        mgr.register_section("identity", SectionStability.STABLE, lambda: "I am the agent")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("context", SectionStability.DYNAMIC, lambda: "recent stuff")

        blocks = mgr.build_prompt_blocks()
        assert len(blocks) == 2
        assert blocks[0].cache_scope == "global"
        assert blocks[0].ttl == "1h"
        assert blocks[1].cache_scope is None
        assert blocks[1].ttl is None

    def test_boundary_marker_not_in_output(self, mgr):
        mgr.register_section("a", SectionStability.STABLE, lambda: "before")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("b", SectionStability.DYNAMIC, lambda: "after")

        blocks = mgr.build_prompt_blocks()
        texts = [b.text for b in blocks]
        assert DYNAMIC_BOUNDARY not in texts

    def test_empty_sections_skipped(self, mgr):
        mgr.register_section("a", SectionStability.STABLE, lambda: "content")
        mgr.register_section("b", SectionStability.STABLE, lambda: "")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("c", SectionStability.DYNAMIC, lambda: "")

        blocks = mgr.build_prompt_blocks()
        assert len(blocks) == 1
        assert blocks[0].text == "content"

    def test_has_dynamic_tools_disables_global_scope(self, mgr):
        mgr.register_section("identity", SectionStability.STABLE, lambda: "I am the agent")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("context", SectionStability.DYNAMIC, lambda: "recent stuff")

        blocks = mgr.build_prompt_blocks(has_dynamic_tools=True)
        assert blocks[0].cache_scope is None
        assert blocks[0].ttl is None

    def test_global_cache_ttl_is_configurable(self):
        mgr = ConsciousnessManager(global_cache_ttl="5m")
        mgr.register_section("identity", SectionStability.STABLE, lambda: "I am the agent")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("context", SectionStability.DYNAMIC, lambda: "recent stuff")

        blocks = mgr.build_prompt_blocks()
        assert blocks[0].cache_scope == "global"
        assert blocks[0].ttl == "5m"
        assert blocks[1].ttl is None

    def test_global_cache_ttl_defaults_to_1h(self):
        mgr = ConsciousnessManager()
        mgr.register_section("identity", SectionStability.STABLE, lambda: "I am the agent")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("context", SectionStability.DYNAMIC, lambda: "recent stuff")

        blocks = mgr.build_prompt_blocks()
        assert blocks[0].ttl == "1h"

    def test_all_stable_no_boundary(self, mgr):
        mgr.register_section("a", SectionStability.STABLE, lambda: "hello")
        mgr.register_section("b", SectionStability.STABLE, lambda: "world")

        blocks = mgr.build_prompt_blocks()
        assert len(blocks) == 2
        assert all(b.cache_scope == "global" for b in blocks)

    def test_multiple_stable_before_boundary(self, mgr):
        mgr.register_section("identity", SectionStability.STABLE, lambda: "identity")
        mgr.register_section("mission", SectionStability.STABLE, lambda: "mission")
        mgr.register_section("budget", SectionStability.STABLE, lambda: "budget")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("ctx", SectionStability.DYNAMIC, lambda: "ctx")

        blocks = mgr.build_prompt_blocks()
        assert len(blocks) == 4
        assert blocks[0].cache_scope == "global"
        assert blocks[1].cache_scope == "global"
        assert blocks[2].cache_scope == "global"
        assert blocks[3].cache_scope is None


class TestSectionHashes:
    def test_hashes_match_content(self, mgr):
        mgr.register_section("a", SectionStability.STABLE, lambda: "hello")
        mgr.resolve_sections()

        hashes = mgr.section_content_hashes()
        expected = hashlib.sha256("hello".encode()).hexdigest()
        assert hashes["a"] == expected

    def test_empty_section_hashes_to_empty_string(self, mgr):
        mgr.register_section("a", SectionStability.STABLE, lambda: "")
        mgr.resolve_sections()

        hashes = mgr.section_content_hashes()
        expected = hashlib.sha256(b"").hexdigest()
        assert hashes["a"] == expected


class TestAnthropicFormatting:
    def test_single_cache_breakpoint_on_last_system_block(self):
        """One cumulative cache breakpoint on the LAST system block caches the
        whole (deploy-stable) system as a single 1h prefix. Earlier blocks
        carry no cache_control, so the system can never overflow Anthropic's
        4-breakpoint cap regardless of how many blocks features add."""
        from src.herald.provider import Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        blocks = [
            SystemPromptBlock(text="stable", cache_scope="global", ttl="1h"),
            SystemPromptBlock(text="dynamic", cache_scope=None, ttl=None),
        ]
        kwargs = provider.build_kwargs(
            messages=[Message(role="user", content="hi")],
            system=blocks,
            tools=None,
        )
        system_param = kwargs["system"]
        assert len(system_param) == 2
        assert system_param[0]["text"] == "stable"
        assert "cache_control" not in system_param[0]
        assert system_param[1]["text"] == "dynamic"
        assert system_param[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_system_breakpoint_ttl_follows_configured_global_cache_ttl(self):
        """Wire layer: the system-breakpoint cache_control ttl is driven by the
        configured settings.global_cache_ttl, not a hardcoded "1h". With
        GLOBAL_CACHE_TTL=5m the produced Anthropic kwargs must carry ttl "5m";
        the default must carry "1h" (behavior unchanged)."""
        from src.herald.provider import Provider
        from src.config import Settings

        blocks = [
            SystemPromptBlock(text="stable", cache_scope="global", ttl="5m"),
            SystemPromptBlock(text="dynamic", cache_scope=None, ttl=None),
        ]

        five_min = Provider(
            Settings(
                cdt_url="http://citadel-test",
                cdt_key="sk-sept-cit-test",
                global_cache_ttl="5m",
            )
        )
        kwargs = five_min.build_kwargs(
            messages=[Message(role="user", content="hi")],
            system=blocks,
            tools=None,
        )
        assert kwargs["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}

        default = Provider(
            Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        )
        kwargs_default = default.build_kwargs(
            messages=[Message(role="user", content="hi")],
            system=blocks,
            tools=None,
        )
        assert kwargs_default["system"][-1]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_cache_scope_never_leaks_to_wire(self):
        """Regression guard: engine-internal cache_scope must never reach the
        wire. A wire block carries only type + text + cache_control."""
        from src.herald.provider import Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)
        kwargs = provider.build_kwargs(
            messages=[Message(role="user", content="hi")],
            system=[SystemPromptBlock(text="t", cache_scope="global", ttl="5m")],
            tools=None,
        )
        for block in kwargs["system"]:
            assert "scope" not in block and "cache_scope" not in block
            assert set(block) <= {"type", "text", "cache_control"}

    def test_system_contributes_one_breakpoint_regardless_of_block_count(self):
        """Even with many system blocks (soul + craft + citation guidance + …),
        the system contributes exactly one cache_control breakpoint — so with
        the tool + message markers the total can never exceed Anthropic's 4."""
        from src.herald.provider import Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)
        blocks = [SystemPromptBlock(text=f"b{i}", ttl="1h") for i in range(5)]
        kwargs = provider.build_kwargs(
            messages=[Message(role="user", content="hi")],
            system=blocks,
            tools=None,
        )
        sys_cc = sum(1 for b in kwargs["system"] if "cache_control" in b)
        assert sys_cc == 1

    def test_empty_blocks_skipped_in_system_param(self):
        from src.herald.provider import Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        blocks = [
            SystemPromptBlock(text="stable", cache_scope="global"),
            SystemPromptBlock(text="", cache_scope=None),
        ]
        kwargs = provider.build_kwargs(
            messages=[Message(role="user", content="hi")],
            system=blocks,
            tools=None,
        )
        assert len(kwargs["system"]) == 1


class TestMessageCacheMarkers:
    def test_exactly_one_marker_on_last_message(self):
        from src.herald.provider import place_message_cache_marker

        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            {"role": "user", "content": [{"type": "text", "text": "bye"}]},
        ]
        place_message_cache_marker(messages)
        assert "cache_control" in messages[2]["content"][-1]
        assert "cache_control" not in messages[0]["content"][-1]
        assert "cache_control" not in messages[1]["content"][-1]

    def test_marker_on_last_assistant_when_last(self):
        from src.herald.provider import place_message_cache_marker

        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]
        place_message_cache_marker(messages)
        assert "cache_control" in messages[1]["content"][-1]
        assert "cache_control" not in messages[0]["content"][-1]

    def test_cache_marker_skips_thinking_blocks(self):
        from src.herald.provider import place_message_cache_marker

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "response"},
                    {"type": "thinking", "thinking": "hmm"},
                ],
            },
        ]
        place_message_cache_marker(messages)
        assert "cache_control" in messages[0]["content"][0]
        assert "cache_control" not in messages[0]["content"][1]

    def test_string_content_converted_and_marked(self):
        from src.herald.provider import place_message_cache_marker

        messages = [
            {"role": "user", "content": "plain string"},
        ]
        place_message_cache_marker(messages)
        assert isinstance(messages[0]["content"], list)
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert messages[0]["content"][0]["text"] == "plain string"


class TestToolSchemaCache:
    def test_tool_cache_reuses_on_same_tools(self):
        from src.herald.provider import Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        tools = [ToolDef(name="foo", description="bar", input_schema={"type": "object"})]
        result1 = provider.get_cached_tools(tools)
        result2 = provider.get_cached_tools(tools)
        assert result1 == result2
        assert provider.cached_tool_names == frozenset(["foo"])

    def test_tool_cache_recomputes_on_different_tools(self):
        from src.herald.provider import Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        tools1 = [ToolDef(name="foo", description="bar", input_schema={"type": "object"})]
        tools2 = [ToolDef(name="baz", description="qux", input_schema={"type": "object"})]
        provider.get_cached_tools(tools1)
        assert provider.cached_tool_names == frozenset(["foo"])
        provider.get_cached_tools(tools2)
        assert provider.cached_tool_names == frozenset(["baz"])


class TestBetaHeaderLatching:
    def test_thinking_latches_on(self):
        from src.herald.provider import apply_thinking, Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        kwargs = {}
        apply_thinking(kwargs, True, provider)
        assert provider.latched_thinking is True
        assert "thinking" in kwargs

    def test_latched_thinking_persists_when_off(self):
        from src.herald.provider import apply_thinking, Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        kwargs1 = {}
        apply_thinking(kwargs1, True, provider)
        assert provider.latched_thinking is True

        kwargs2 = {}
        apply_thinking(kwargs2, False, provider)
        assert "thinking" in kwargs2

    def test_no_latch_without_super_thinking(self):
        from src.herald.provider import apply_thinking, Provider
        from src.config import Settings

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)

        kwargs = {}
        apply_thinking(kwargs, False, provider)
        assert provider.latched_thinking is False
        assert "thinking" not in kwargs


class TestCacheBreakDetection:
    def test_record_prompt_state_hashes_blocks(self):
        blocks = [
            SystemPromptBlock(text="hello", cache_scope="global", ttl="1h"),
            SystemPromptBlock(text="world", cache_scope=None),
        ]
        state = record_prompt_state(blocks, tools=None)
        assert state.system_hash_with_cache
        assert state.system_hash_without_cache
        assert state.system_hash_with_cache != state.system_hash_without_cache

    def test_per_tool_schema_hashing(self):
        tools = [
            ToolDef(name="foo", description="d1", input_schema={"type": "object"}),
            ToolDef(name="bar", description="d2", input_schema={"type": "string"}),
        ]
        state = record_prompt_state(system_blocks=None, tools=tools)
        assert "foo" in state.tool_hashes
        assert "bar" in state.tool_hashes
        assert state.tool_hashes["foo"] != state.tool_hashes["bar"]

    def test_no_break_when_cache_healthy(self):
        state = PromptState(prev_cache_read_tokens=1000)
        usage = Usage(cache_read_input_tokens=900)
        result = check_response_for_cache_break(state, usage)
        assert result is None

    def test_break_detected_on_drop(self):
        state = PromptState(prev_cache_read_tokens=1000)
        usage = Usage(cache_read_input_tokens=0)
        result = check_response_for_cache_break(state, usage)
        assert result is not None
        assert result.break_type == CacheBreakType.SERVER_TTL

    def test_content_change_detected(self):
        prev = PromptState(
            system_hash_with_cache="aaa",
            system_hash_without_cache="bbb",
            prev_cache_read_tokens=1000,
        )
        curr = PromptState(
            system_hash_with_cache="ccc",
            system_hash_without_cache="ddd",
            prev_cache_read_tokens=1000,
        )
        usage = Usage(cache_read_input_tokens=0)
        result = check_response_for_cache_break(curr, usage, prev_state=prev)
        assert result is not None
        assert result.break_type == CacheBreakType.CLIENT_CONTENT

    def test_scope_change_detected(self):
        prev = PromptState(
            system_hash_with_cache="aaa",
            system_hash_without_cache="same",
            prev_cache_read_tokens=1000,
        )
        curr = PromptState(
            system_hash_with_cache="bbb",
            system_hash_without_cache="same",
            prev_cache_read_tokens=1000,
        )
        usage = Usage(cache_read_input_tokens=0)
        result = check_response_for_cache_break(curr, usage, prev_state=prev)
        assert result is not None
        assert result.break_type == CacheBreakType.CLIENT_SCOPE

    def test_tool_change_detected(self):
        prev = PromptState(
            system_hash_with_cache="same",
            system_hash_without_cache="same",
            tool_hashes={"foo": "hash1"},
            prev_cache_read_tokens=1000,
        )
        curr = PromptState(
            system_hash_with_cache="same",
            system_hash_without_cache="same",
            tool_hashes={"foo": "hash2"},
            prev_cache_read_tokens=1000,
        )
        usage = Usage(cache_read_input_tokens=0)
        result = check_response_for_cache_break(curr, usage, prev_state=prev)
        assert result is not None
        assert result.break_type == CacheBreakType.CLIENT_TOOLS
        assert "foo" in result.changed_tools


class TestFalsePositiveSuppression:
    def test_notify_and_consume(self):
        notify_cache_deletion("main")
        assert is_cache_deletion_pending("main")
        assert consume_pending_cache_deletion("main")
        assert not is_cache_deletion_pending("main")

    def test_consume_clears_flag(self):
        notify_cache_deletion("main")
        consume_pending_cache_deletion("main")
        assert not consume_pending_cache_deletion("main")

    def test_expected_break_after_compaction(self):
        notify_cache_deletion("main")
        state = PromptState(prev_cache_read_tokens=1000)
        usage = Usage(cache_read_input_tokens=0)
        result = check_response_for_cache_break(state, usage, source="main")
        assert result is not None
        assert result.break_type == CacheBreakType.EXPECTED

    def test_per_source_isolation(self):
        notify_cache_deletion("main")
        assert not is_cache_deletion_pending("subagent:1")
        assert is_cache_deletion_pending("main")


class TestProviderFlatten:
    def test_flatten_system_blocks(self):
        from src.herald.types import flatten_system_blocks

        blocks = [
            SystemPromptBlock(text="hello"),
            SystemPromptBlock(text="world"),
        ]
        assert flatten_system_blocks(blocks) == "hello\n\nworld"

    def test_flatten_none(self):
        from src.herald.types import flatten_system_blocks

        assert flatten_system_blocks(None) == ""

    def test_flatten_empty_blocks(self):
        from src.herald.types import flatten_system_blocks

        blocks = [
            SystemPromptBlock(text="hello"),
            SystemPromptBlock(text=""),
            SystemPromptBlock(text="world"),
        ]
        assert flatten_system_blocks(blocks) == "hello\n\nworld"


class TestSystemPromptBlock:
    def test_frozen(self):
        block = SystemPromptBlock(text="hello", cache_scope="global", ttl="1h")
        with pytest.raises(AttributeError):
            block.text = "changed"

    def test_defaults(self):
        block = SystemPromptBlock(text="hello")
        assert block.cache_scope is None
        assert block.ttl is None

    def test_equality(self):
        a = SystemPromptBlock(text="hello", cache_scope="global", ttl="1h")
        b = SystemPromptBlock(text="hello", cache_scope="global", ttl="1h")
        assert a == b


class TestBetaLatchCompactReset:
    def test_post_compact_clears_thinking_latch(self):
        from src.herald.provider import Provider
        from src.config import Settings
        from src.engine_core.post_compact_cleanup import run_post_compact_cleanup

        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        provider = Provider(settings)
        provider.latched_thinking = True

        run_post_compact_cleanup(llm_provider=provider)
        assert provider.latched_thinking is False

    def test_post_compact_tolerates_no_provider(self):
        from src.engine_core.post_compact_cleanup import run_post_compact_cleanup

        run_post_compact_cleanup(llm_provider=None)


class TestConsciousnessIntegration:
    def test_full_section_lifecycle(self, mgr):
        volatile_counter = 0

        def volatile_compute():
            nonlocal volatile_counter
            volatile_counter += 1
            return f"mcp-{volatile_counter}"

        mgr.register_section("identity", SectionStability.STABLE, lambda: "I am agent")
        mgr.register_section("mission", SectionStability.STABLE, lambda: "goals here")
        mgr.register_section("__boundary__", SectionStability.STABLE, lambda: DYNAMIC_BOUNDARY)
        mgr.register_section("context", SectionStability.DYNAMIC, lambda: "recent episodes")
        mgr.register_section("mcp", SectionStability.VOLATILE, volatile_compute)

        blocks1 = mgr.build_prompt_blocks()
        assert len(blocks1) == 4
        assert blocks1[0].text == "I am agent"
        assert blocks1[0].cache_scope == "global"
        assert blocks1[3].text == "mcp-1"

        blocks2 = mgr.build_prompt_blocks()
        assert blocks2[3].text == "mcp-2"
        assert blocks2[0].text == "I am agent"

        mgr.invalidate()
        blocks3 = mgr.build_prompt_blocks()
        assert blocks3[3].text == "mcp-3"

    def test_invalidate_all_clears_both(self, mgr):
        mgr.register_section("s", SectionStability.STABLE, lambda: "content")
        mgr.resolve_sections()
        assert "s" in mgr.section_cache

        mgr.invalidate_all()
        assert len(mgr.section_cache) == 0
        assert len(mgr.cache) == 0
