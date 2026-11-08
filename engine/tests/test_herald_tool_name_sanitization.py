"""Tool name sanitization at the Anthropic provider boundary.

Anthropic's Messages API enforces ``^[a-zA-Z0-9_-]{1,128}$`` on tool names
and 400s on internal forms like ``ad:gmail.send_message`` (colons + dots).
Engine code uses the internal form; the provider has to sanitize on the way
out, keep a sanitised → internal reverse map, and translate inbound tool_use
responses back. These tests guard each side of that contract.

Regression context: pre-fix, ``allow_assets=true`` exposing any ``ad:``-
prefixed tool caused the Anthropic provider to silently 400. Coordinator
logged "Thread execution failed", user saw their prompt and no response.
"""

from src.config import Settings
from src.herald.provider import (
    Provider,
    blocks_to_wire,
    to_wire_tools,
)
from src.herald.types import (
    ToolDef,
    ToolUseBlock,
    build_tool_name_map,
    sanitize_tool_name,
)


def tool(name: str, *, defer: bool = False) -> ToolDef:
    return ToolDef(
        name=name,
        description="t",
        input_schema={"type": "object", "properties": {}},
        defer_loading=defer,
    )


class TestSanitizeOutbound:
    def test_to_wire_tools_sanitizes_colon_and_dot(self):
        out = to_wire_tools([tool("ad:gmail.send_message")])
        assert out[0]["name"] == "ad_gmail_send_message"

    def test_to_wire_tools_preserves_safe_names(self):
        out = to_wire_tools([tool("read_file")])
        assert out[0]["name"] == "read_file"

    def test_to_wire_tools_preserves_description_and_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        tool = ToolDef(name="ad:foo.bar", description="explain", input_schema=schema)
        out = to_wire_tools([tool])
        assert out[0]["description"] == "explain"
        assert out[0]["input_schema"] == schema

    def test_to_wire_tools_preserves_defer_loading_flag(self):
        out = to_wire_tools([tool("ad:slow.thing", defer=True)])
        assert out[0]["name"] == "ad_slow_thing"
        assert out[0]["defer_loading"] is True

    def test_blocks_to_wire_sanitizes_tool_use_name(self):
        blocks = [ToolUseBlock(id="tu_1", name="ad:gmail.send_message", input={"to": "a@b"})]
        out = blocks_to_wire(blocks)
        assert out == [
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "ad_gmail_send_message",
                "input": {"to": "a@b"},
            }
        ]


class TestReverseMapRoundTrip:
    def test_build_tool_name_map_maps_sanitised_to_internal(self):
        tools = [tool("ad:gmail.send_message"), tool("read_file")]
        m = build_tool_name_map(tools)
        assert m["ad_gmail_send_message"] == "ad:gmail.send_message"
        assert m["read_file"] == "read_file"

    def test_sanitize_then_reverse_round_trip(self):
        names = ["ad:gmail.send_message", "ad:fs.read", "plain", "with-hyphen"]
        tools = [tool(n) for n in names]
        m = build_tool_name_map(tools)
        for n in names:
            assert m[sanitize_tool_name(n)] == n


class TestProviderInstanceReverseMap:
    """The provider keeps a per-instance reverse map alongside the tool schema
    cache. get_cached_tools is the refresh point — populates it on the first
    call and again whenever the tool set changes."""

    def provider(self) -> Provider:
        settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
        return Provider(settings)

    def test_reverse_map_empty_before_first_call(self):
        provider = self.provider()
        assert provider.tool_name_reverse_map == {}

    def test_reverse_map_populated_on_first_get(self):
        provider = self.provider()
        provider.get_cached_tools([tool("ad:gmail.send_message"), tool("read_file")])
        assert provider.tool_name_reverse_map == {
            "ad_gmail_send_message": "ad:gmail.send_message",
            "read_file": "read_file",
        }

    def test_reverse_map_refreshes_on_tool_set_change(self):
        provider = self.provider()
        provider.get_cached_tools([tool("ad:gmail.send_message")])
        provider.get_cached_tools([tool("ad:fs.read")])
        assert provider.tool_name_reverse_map == {
            "ad_fs_read": "ad:fs.read",
        }
        assert "ad_gmail_send_message" not in provider.tool_name_reverse_map

    def test_reverse_map_unchanged_when_tool_set_unchanged(self):
        provider = self.provider()
        provider.get_cached_tools([tool("ad:gmail.send_message")])
        before = dict(provider.tool_name_reverse_map)
        provider.get_cached_tools([tool("ad:gmail.send_message")])
        assert provider.tool_name_reverse_map == before
