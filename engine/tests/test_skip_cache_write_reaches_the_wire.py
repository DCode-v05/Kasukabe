"""skip_cache_write must change the request, not just the signature.

The flag was declared on the LLM protocol, on the concrete provider, and at
three call sites, and was then dropped before build_kwargs - so the request was
byte-identical whether it was True or False and the whole feature was inert
with nothing to see in a log.

These tests assert on the kwargs handed to the transport client, reached
through the REAL provider entry points (generate_with_tools / stream_with_tools),
so a future refactor that stops forwarding the flag fails here.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.herald.provider import Provider
from src.herald.types import Message, SystemPromptBlock, ToolDef


class CapturingClient:
    """Stands in for the Citadel transport; records the kwargs it was handed."""

    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def post_messages(self, kwargs, extra_headers=None):
        self.kwargs = kwargs
        return {
            "id": "msg_1",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    async def stream_messages(self, kwargs, extra_headers=None):
        self.kwargs = kwargs
        yield {
            "type": "message_start",
            "message": {"id": "msg_1", "usage": {"input_tokens": 1}},
        }
        yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
        yield {"type": "message_stop"}


def make_provider() -> tuple[Provider, CapturingClient]:
    settings = Settings(cdt_url="http://citadel-test", cdt_key="sk-sept-cit-test")
    provider = Provider(settings)
    client = CapturingClient()
    provider.client = client
    return provider, client


SYSTEM = [SystemPromptBlock(text="stable soul", cache_scope="global", ttl="1h")]
MESSAGES = [Message(role="user", content="summarise the thread")]
TOOLS = [
    ToolDef(
        name="noop",
        description="does nothing",
        input_schema={"type": "object", "properties": {}},
    )
]


def cache_control_sites(kwargs: dict) -> list[str]:
    """Every place a cache_control breakpoint landed in a request."""
    sites: list[str] = []
    for i, block in enumerate(kwargs.get("system") or []):
        if "cache_control" in block:
            sites.append(f"system[{i}]")
    for i, tool in enumerate(kwargs.get("tools") or []):
        if "cache_control" in tool:
            sites.append(f"tools[{i}]")
    for i, msg in enumerate(kwargs.get("messages") or []):
        content = msg.get("content")
        if isinstance(content, list):
            for j, block in enumerate(content):
                if isinstance(block, dict) and "cache_control" in block:
                    sites.append(f"messages[{i}].content[{j}]")
    return sites


class TestBuildKwargs:
    def test_default_places_breakpoints_on_system_tools_and_messages(self):
        provider, _ = make_provider()
        kwargs = provider.build_kwargs(MESSAGES, SYSTEM, TOOLS)
        sites = cache_control_sites(kwargs)
        assert any(s.startswith("system") for s in sites)
        assert any(s.startswith("tools") for s in sites)
        assert any(s.startswith("messages") for s in sites)

    def test_skip_cache_write_removes_every_breakpoint(self):
        provider, _ = make_provider()
        kwargs = provider.build_kwargs(MESSAGES, SYSTEM, TOOLS, skip_cache_write=True)
        assert cache_control_sites(kwargs) == []

    def test_only_the_breakpoints_differ(self):
        """Suppression must not change what the model is asked, only caching."""
        provider, _ = make_provider()
        on = provider.build_kwargs(MESSAGES, SYSTEM, TOOLS)
        off = provider.build_kwargs(MESSAGES, SYSTEM, TOOLS, skip_cache_write=True)
        assert on["model"] == off["model"]
        assert [b["text"] for b in on["system"]] == [b["text"] for b in off["system"]]
        assert [t["name"] for t in on["tools"]] == [t["name"] for t in off["tools"]]


class TestEntryPointsForwardTheFlag:
    @pytest.mark.asyncio
    async def test_generate_with_tools_forwards_it(self):
        provider, client = make_provider()

        await provider.generate_with_tools(MESSAGES, SYSTEM, TOOLS)
        assert cache_control_sites(client.kwargs) != []

        await provider.generate_with_tools(MESSAGES, SYSTEM, TOOLS, skip_cache_write=True)
        assert cache_control_sites(client.kwargs) == []

    @pytest.mark.asyncio
    async def test_generate_with_tools_forwards_it_without_tools(self):
        """The summarizer's shape: tools=[], which is where the waste was paid."""
        provider, client = make_provider()

        await provider.generate_with_tools(MESSAGES, SYSTEM, [], skip_cache_write=True)
        assert cache_control_sites(client.kwargs) == []
        assert "tools" not in client.kwargs

    @pytest.mark.asyncio
    async def test_stream_with_tools_forwards_it(self):
        provider, client = make_provider()

        async for _ in provider.stream_with_tools(MESSAGES, SYSTEM, TOOLS):
            pass
        assert cache_control_sites(client.kwargs) != []

        async for _ in provider.stream_with_tools(
            MESSAGES, SYSTEM, TOOLS, skip_cache_write=True
        ):
            pass
        assert cache_control_sites(client.kwargs) == []
